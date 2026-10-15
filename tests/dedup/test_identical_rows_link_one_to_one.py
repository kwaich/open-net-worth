"""Regression: B11 — a statement carrying N content-identical rows (same date, amount,
description) against N (or more) identical existing ledger rows must link EVERY row to a
DISTINCT ledger id, one-to-one.

Root cause (historical): ``_statement_refs`` fabricated two refs per draft, and
``disambiguate_identical_rows`` suffixed only ``entries[0]``, so a repeat's second ref stayed
byte-identical across siblings. ``matching._batch_l0`` reads every ref pair, so it matched a
repeat against its SIBLING statement row instead of a free ledger candidate and only ~2 of N rows
ever linked. A draft now carries ONE ref (the legacy form was retired 2026-10-06); the fix's
invariant is unchanged: every ref of a repeat takes the same occurrence index, so a repeat never
collides with a sibling.

All data below is synthetic — a fake account id, a fake vending-machine description, round
dollar amounts."""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import matching
from backend.services.import_common import RowResult, disambiguate_identical_rows, flag_duplicates
from backend.services.statements import importer
from backend.services.statements.canonical import CanonicalRow

ACCT = "acc_test"
DAY = "2026-03-05"
DESC = "VENDING MACHINE 12"
AMOUNTS = [1.50, 1.50, 1.50, 1.50, 2.50]


@pytest.fixture(autouse=True)
def env(db_isolated):
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": ACCT}])


def _ledger_twin(i: int, amt: float) -> None:
    ledger_service.transactions.insert(
        {
            "id": f"txn_test{i:02d}",
            "kind": "expense",
            "date": DAY,
            "description": "Vending Machine",
            "from_account_id": ACCT,
            "from_amount": amt,
            "from_currency": "SGD",
            "source": "manual",
        }
    )


def _drafts(amounts: list[float]) -> list[RowResult]:
    results = []
    for i, amt in enumerate(amounts, start=1):
        row = CanonicalRow(
            date=DAY,
            description=DESC,
            payee="Vending Co",
            amount=amt,
            direction="OUT",
            currency="SGD",
        )
        draft = importer._row_to_draft(row, ACCT, "SGD", "pdf")
        results.append(RowResult(line=i, draft=draft))
    return results


def test_disambiguate_suffixes_every_entry_of_a_repeat():
    results = _drafts(AMOUNTS)
    disambiguate_identical_rows(results)

    refs_by_row = [[e["ref"] for e in r.draft["external_refs"]] for r in results]
    # 4 identical 1.50 rows: first bare, the rest suffixed on EVERY entry (not just entries[0]).
    for row_refs in refs_by_row[:4]:
        assert len(row_refs) == 1, "each draft carries exactly one deterministic ref"
    base_refs = refs_by_row[0]
    for n, row_refs in enumerate(refs_by_row[1:4], start=1):
        for base, suffixed in zip(base_refs, row_refs):
            assert suffixed == f"{base}:{n}", (base, suffixed)
    # The lone 2.50 row is untouched (no sibling to collide with): its bare ref.
    expected_primary = importer.external_ref(ACCT, DAY, DESC, 2.50, False, None)
    assert refs_by_row[4] == [expected_primary]


def test_same_day_same_amount_rows_with_different_text_suffix_in_file_order():
    """Two DIFFERENT rows can share a ref (``external_ref`` hashes only account|date|amount).
    With the legacy ref retired (2026-10-06) nothing else tells a $5 coffee from a $5 bus ride, so
    the second is suffixed in file order — stable for the same file, swapped if the file is
    reordered. Pins the accepted cost of dropping the content-hashed second ref."""
    coffee = CanonicalRow(
        date=DAY,
        description="COFFEE",
        payee="Coffee Co",
        amount=5.0,
        direction="OUT",
        currency="SGD",
        reference="REF-A",
    )
    bus = CanonicalRow(
        date=DAY,
        description="BUS",
        payee="Transit Co",
        amount=5.0,
        direction="OUT",
        currency="SGD",
        reference="REF-B",
    )
    results = [
        RowResult(line=1, draft=importer._row_to_draft(coffee, ACCT, "SGD", "pdf")),
        RowResult(line=2, draft=importer._row_to_draft(bus, ACCT, "SGD", "pdf")),
    ]
    disambiguate_identical_rows(results)

    refs = [[e["ref"] for e in r.draft["external_refs"]] for r in results]
    assert all(len(r) == 1 for r in refs)
    assert refs[1] == [f"{refs[0][0]}:1"], "the shared ref suffixes on the 2nd row"


def test_identical_rows_link_one_to_one():
    for i, amt in enumerate(AMOUNTS):
        _ledger_twin(i, amt)

    results = _drafts(AMOUNTS)
    disambiguate_identical_rows(results)
    flag_duplicates(results)
    assert not any(r.duplicate for r in results), "disambiguation must clear false duplicates"

    rows = [r.draft for r in results]
    ctx = matching.BatchContext(
        complete=False, window=("2026-03-01", "2026-03-31"), account_ids=frozenset({ACCT})
    )
    row_matches = matching.match_batch(rows, context=ctx, weak_tier=True)

    linked = [rm.assigned.id for rm in row_matches if rm.assigned and rm.assigned.id]
    assert len(linked) == len(AMOUNTS), f"expected every row linked, got {linked}"
    assert len(set(linked)) == len(AMOUNTS), "every row must link to a DISTINCT ledger id"
