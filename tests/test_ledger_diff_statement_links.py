"""The two SERVER-COMPUTED fields on every "In your ledger, not on this statement" entry
(owner, 2026-07-31: "show the description + any statement that these transactions already
belong to", "show that these are near cut off dates", "please handle these on the backend, not
client so the ai tool can get the same information").

- ``on_statements`` — which statement(s) the entry is ALREADY on, resolved from
                      ``external_refs``. The FIRST cut resolved it from ``batch_id``
                      instead and shipped the owner "Already on: Nimbus Cloud USD invoices 30 May
                      – 7 Jun 2026" for a row whose statement was `eStatement_Jun2026 2.pdf` —
                      a truthful answer to a different question ("where did it come FROM").
                      A merge never stamps ``batch_id``, so that link is blind to every
                      row a statement LINKED rather than created; on a real book that can be a
                      large minority of the statement-linked rows (measured on one real book:
                      62 of 210).
- ``near_cutoff``  — which window edge it sits near and by how many days. Pinned to the
                     EXISTING boundary bucket: non-None must be exactly "filed under boundary",
                     so nobody can introduce a second threshold that disagrees with the first.

The description itself is NOT re-tested here — it already rides as ``note`` on the shared
``_existing_summary`` projection; ``test_import_anomalies*`` owns that.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services.statements.ledger_diff import _BOUNDARY_DAYS, _collect_uncovered
from backend.services.statements.ledger_statement_links import (
    _human_range,
    bank_reference,
    statement_label,
)

# The printed period 1–30 Jun widened by _PERIOD_SLACK_DAYS, i.e. what `_window` returns.
LO, HI = "2026-05-31", "2026-07-01"
ACCT = "acc_test"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": ACCT}])


def _add_txn(txn_id: str, day: str, **extra) -> None:
    ledger_service.transactions.insert(
        {
            "id": txn_id,
            "date": day,
            "kind": "expense",
            "from_account_id": ACCT,
            "from_amount": 12.5,
            "from_currency": "SGD",
            "description": "GRAB* A-6DKJH2 SINGAPORE",
            "source": "manual",
            **extra,
        }
    )


def _add_statement(statement_id: str, rows: list[dict] | None = None, **extra) -> None:
    """A statement record shaped like the decision pipeline leaves it: `import_rows` is the
    cached canonical parse, which is what the ref index re-derives refs from."""
    from backend.db.statement_store import StatementStore

    StatementStore().insert(
        {"id": statement_id, "account_id": ACCT, "import_rows": rows or [], **extra}
    )


def _row(day: str, description: str, amount: float, reference: str | None = None) -> dict:
    return {
        "date": day,
        "direction": "OUT",
        "amount": amount,
        "description": description,
        "reference": reference,
    }


def _ref_for(day: str, description: str, amount: float, reference: str | None = None) -> str:
    """The ref the importer would mint for that row — the exact string a booked transaction
    carries in `external_refs`. Uses the REAL minting function, never a restatement."""
    from backend.services.statements.importer import external_ref

    return external_ref(ACCT, day, description, amount, False, reference)


def _refs_for(
    day: str, description: str, amount: float, reference: str | None = None
) -> list[dict]:
    """Every ref a booked row carries: its deterministic identity."""
    from backend.services.statements.importer import external_ref

    return [
        {"source": "xlsx", "ref": external_ref(ACCT, day, description, amount, False, reference)}
    ]


def _collect(boundary_days: int = _BOUNDARY_DAYS) -> dict:
    return _collect_uncovered(LO, HI, {ACCT}, set(), boundary_days=boundary_days)


def _only(bucket: str) -> dict:
    out = _collect()
    assert len(out[bucket]) == 1, out
    return out[bucket][0]


# ── on_statements: which statement is it actually ON ─────────────────────────────────────


def test_a_row_a_statement_LINKED_still_resolves_to_that_statement():
    """THE regression. The statement linked this row rather than creating it, so the row keeps
    the `batch_id` of whatever booked it first (an email agent) and the statement's ref
    was APPENDED. Batch-derived provenance cannot see this; ref-derived can."""
    _add_statement(
        "stmt_jun",
        rows=[_row("2026-06-03", "Nimbus Cloud", 16.20)],
        filename="eStatement_Jun2026 2.pdf",
        import_meta={"period_start": "2026-05-05", "period_end": "2026-06-04"},
    )
    _add_txn(
        "t1",
        "2026-06-03",
        description="Nimbus Cloud",
        source="email",
        batch_id="imp_nimbus_invoices",  # the INVOICE batch — a different question
        external_refs=[{"source": "pdf", "ref": _ref_for("2026-06-03", "Nimbus Cloud", 16.20)}],
    )

    on = _only("rows")["on_statements"]
    assert on["label"] == "5 May – 4 Jun 2026 statement"
    assert [s["statement_id"] for s in on["statements"]] == ["stmt_jun"]
    assert on["statements"][0]["filename"] == "eStatement_Jun2026 2.pdf"


def test_the_import_batch_never_stands_in_for_a_statement():
    """The owner's exact complaint: an invoice-import batch rendered as "Already on …". With no
    ref pointing at a statement record, the honest answer is nothing at all."""
    _add_txn("t1", "2026-06-15", batch_id="imp_nimbus_invoices")

    row = _only("rows")
    assert row["on_statements"] is None
    assert "origin" not in row  # the batch-derived field is gone, not merely unrendered


@pytest.mark.parametrize(
    "extra",
    [
        {"source": "manual"},
        {"source": "ai"},
        {"source": "simplefin"},
        {"external_refs": [{"source": "email", "ref": "ea504bf64e4d"}]},
    ],
)
def test_a_row_on_no_statement_we_hold_reports_none(extra):
    _add_statement("stmt_jun", rows=[_row("2026-06-03", "Something else", 9.0)])
    _add_txn("t1", "2026-06-15", **extra)

    assert _only("rows")["on_statements"] is None


def test_a_row_on_two_statements_reports_both_and_says_so_in_the_label():
    """A real book can have a handful of rows that sit on two statements. Neither is silently
    dropped."""
    shared = _ref_for("2026-06-03", "Nimbus Cloud", 16.20)
    _add_statement(
        "stmt_jun",
        rows=[_row("2026-06-03", "Nimbus Cloud", 16.20)],
        import_meta={"period_start": "2026-05-05", "period_end": "2026-06-04"},
    )
    _add_statement(
        "stmt_jul",
        rows=[_row("2026-06-03", "Nimbus Cloud", 16.20)],
        import_meta={"period_start": "2026-06-05", "period_end": "2026-07-04"},
    )
    _add_txn("t1", "2026-06-03", external_refs=[{"source": "pdf", "ref": shared}])

    on = _only("rows")["on_statements"]
    assert len(on["statements"]) == 2
    # The cycle whose printed period CONTAINS 3 Jun leads; the other is counted, not hidden.
    assert on["statements"][0]["statement_id"] == "stmt_jun"
    assert on["label"] == "5 May – 4 Jun 2026 statement +1 more"


def test_the_containing_cycle_wins_over_the_more_recent_one():
    shared = _ref_for("2026-06-20", "Nimbus Cloud", 16.20)
    _add_statement(
        "stmt_covers",
        rows=[_row("2026-06-20", "Nimbus Cloud", 16.20)],
        import_meta={"period_start": "2026-06-05", "period_end": "2026-07-04"},
    )
    _add_statement(
        "stmt_later",
        rows=[_row("2026-06-20", "Nimbus Cloud", 16.20)],
        import_meta={"period_start": "2026-07-05", "period_end": "2026-08-04"},
    )
    _add_txn("t1", "2026-06-20", external_refs=[{"source": "pdf", "ref": shared}])

    assert _only("rows")["on_statements"]["statements"][0]["statement_id"] == "stmt_covers"


def test_three_identical_rows_all_resolve_to_the_statement():
    """B11 follow-up regression: the index used to re-derive BARE refs independent of the
    occurrence-index suffix ``disambiguate_identical_rows`` stamps on a repeat's whole ref set at
    commit time — so a repeat's SUFFIXED ledger ref (2nd, 3rd row) never matched the index and
    ``on_statements`` came back None for every row but the first. The index must derive refs
    through the SAME disambiguation the draft pipeline runs, so it always matches what a commit
    actually wrote."""
    from backend.services.import_common import RowResult, disambiguate_identical_rows
    from backend.services.statements import importer
    from backend.services.statements.canonical import CanonicalRow

    rows = [_row("2026-06-10", "Vending Machine", 1.5) for _ in range(3)]
    _add_statement("stmt_jun", rows=rows, filename="june.pdf")

    results = [
        RowResult(line=i, draft=importer._row_to_draft(CanonicalRow(**r), ACCT, "SGD", "pdf"))
        for i, r in enumerate(rows, start=1)
    ]
    disambiguate_identical_rows(results)
    for i, r in enumerate(results):
        _add_txn(
            f"t{i}",
            "2026-06-10",
            description="Vending Machine",
            external_refs=r.draft["external_refs"],
        )

    out = _collect()
    assert len(out["rows"]) == 3
    for row in out["rows"]:
        assert row["on_statements"] is not None, row
        assert row["on_statements"]["statements"][0]["statement_id"] == "stmt_jun"


def test_a_stale_key_on_a_cached_row_never_breaks_the_whole_index():
    """A cached parse can predate a ``CanonicalRow`` field change. One unknown key on one row
    must be ignored — not raise ``TypeError`` inside ``StatementRefIndex`` and 500 the
    statements panel for EVERY transaction."""
    row = _row("2026-06-10", "Vending Machine", 1.5)
    stale = {**row, "field_from_a_future_version": "x"}
    _add_statement("stmt_jun", rows=[stale], filename="june.pdf")
    _add_txn(
        "t0",
        "2026-06-10",
        description="Vending Machine",
        external_refs=_refs_for("2026-06-10", "Vending Machine", 1.5),
    )

    assert _only("rows")["on_statements"]["statements"][0]["statement_id"] == "stmt_jun"


def test_a_format_native_ref_on_the_cached_row_is_indexed_too():
    """OFX/CAMT/MT940 carry their own id on the canonical row, and `_row_to_draft` PREFERS it —
    so the index must key on that shape as well as the minted one."""
    _add_statement(
        "stmt_ofx",
        rows=[
            {
                **_row("2026-06-10", "Taxi", 22.0),
                "external_refs": [{"source": "ofx", "ref": "FITID-9931"}],
            }
        ],
        filename="june.ofx",
    )
    _add_txn("t1", "2026-06-10", external_refs=[{"source": "ofx", "ref": "FITID-9931"}])

    assert _only("rows")["on_statements"]["statements"][0]["statement_id"] == "stmt_ofx"


def test_a_statement_with_no_cached_parse_indexes_nothing_and_never_false_matches():
    """Most records on an older book predate the decision pipeline and hold no
    `import_rows`. They must contribute no refs rather than match by accident."""
    _add_statement("stmt_legacy", rows=[], filename="old.pdf")
    _add_txn("t1", "2026-06-15", external_refs=[{"source": "pdf", "ref": "whatever"}])

    assert _only("rows")["on_statements"] is None


# ── the bank reference inside a ref ──────────────────────────────────────────────────────


def test_the_bank_reference_rides_along_when_the_feed_carried_one():
    _add_statement(
        "stmt_1",
        rows=[_row("2026-06-15", "Thanh toán GRAB", 12.5, reference="100000000001")],
        filename="riverside.xlsx",
    )
    _add_txn(
        "t1",
        "2026-06-15",
        description="Thanh toán GRAB",
        external_refs=_refs_for("2026-06-15", "Thanh toán GRAB", 12.5, "100000000001"),
    )

    on = _only("rows")["on_statements"]
    assert on["reference"] == "100000000001"
    # The opaque account id inside the ref must never reach a user-facing surface.
    assert "acc_" not in str(on)


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("ref:acc_8adf1b690000:100000000001", "100000000001"),
        ("ref:acc_1:24294266155246571539109", "24294266155246571539109"),
        # OUR content hash when the feed carried no bank reference — a dedup key, not a
        # reference. Printing it would be worse than printing nothing.
        ("f034e64cafb84a596fb854a5", None),
        # A legacy `<fmt>:<ref>` row: not the documented prefixed shape.
        ("QA-STATEMENT-124:qa-import-003", None),
        ("ref:acc_1:", None),
        ("", None),
        (None, None),
    ],
)
def test_bank_reference_parses_only_the_documented_prefixed_shape(ref, expected):
    assert bank_reference(ref) == expected


def test_no_bank_reference_still_names_the_statement():
    """The June PDF's rows are all content-hash refs; the statement is still identified."""
    _add_statement("stmt_1", rows=[_row("2026-06-15", "Nimbus Cloud", 9.40)], filename="jun.pdf")
    _add_txn(
        "t1",
        "2026-06-15",
        description="Nimbus Cloud",
        external_refs=[{"source": "pdf", "ref": _ref_for("2026-06-15", "Nimbus Cloud", 9.40)}],
    )

    on = _only("rows")["on_statements"]
    assert on["reference"] is None
    assert on["label"] == "jun.pdf"


# ── near_cutoff: which edge, how far ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "day,edge,days,label",
    [
        ("2026-05-31", "start", 0, "Opening cut-off"),
        ("2026-06-01", "start", 1, "1 day after opening"),
        ("2026-06-02", "start", 2, "2 days after opening"),
        ("2026-06-29", "end", 2, "2 days before closing"),
        ("2026-06-30", "end", 1, "1 day before closing"),
        ("2026-07-01", "end", 0, "Closing cut-off"),
    ],
)
def test_near_cutoff_names_the_edge_and_the_distance(day, edge, days, label):
    _add_txn("t1", day)

    row = _only("boundary")
    assert row["near_cutoff"] == {"edge": edge, "days": days, "label": label}


def test_an_entry_clear_of_both_edges_carries_no_near_cutoff():
    _add_txn("t1", "2026-06-15")

    assert _only("rows")["near_cutoff"] is None


def test_near_cutoff_is_the_boundary_bucket_not_a_second_threshold():
    """The one guarantee that keeps the flag and the bucket from ever disagreeing: a row is
    filed under ``boundary`` IF AND ONLY IF it carries a ``near_cutoff``."""
    for i, day in enumerate(
        ["2026-05-31", "2026-06-01", "2026-06-03", "2026-06-15", "2026-06-28", "2026-07-01"]
    ):
        _add_txn(f"t{i}", day)

    out = _collect()
    assert [r["date"] for r in out["boundary"]] == ["2026-05-31", "2026-06-01", "2026-07-01"]
    assert all(r["near_cutoff"] is not None for r in out["boundary"])
    assert all(r["near_cutoff"] is None for r in out["rows"])
    assert [r["date"] for r in out["rows"]] == ["2026-06-03", "2026-06-15", "2026-06-28"]


def test_a_self_referential_window_flags_nothing_and_buckets_nothing():
    """``boundary_days=0`` is how ``preview_ledger_check`` disables the bucket for a window
    derived from the statement's own rows. The flag must switch off with it — otherwise the
    UI would cry "near the cut-off" on a window that is near its own edges by construction."""
    _add_txn("t1", "2026-05-31")
    _add_txn("t2", "2026-07-01")

    out = _collect(boundary_days=0)
    assert out["boundary"] == []
    assert [r["near_cutoff"] for r in out["rows"]] == [None, None]


# ── the human labels themselves ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2026-06-03", "2026-06-29", "3–29 Jun 2026"),
        ("2026-06-03", "2026-07-01", "3 Jun – 1 Jul 2026"),
        ("2025-12-28", "2026-01-27", "28 Dec 2025 – 27 Jan 2026"),
        ("2026-06-03", None, None),
        ("not-a-date", "2026-07-01", None),
    ],
)
def test_human_range_writes_shared_units_once(start, end, expected):
    assert _human_range(start, end) == expected


def test_statement_label_prefers_the_printed_period_then_the_date_then_the_filename():
    assert statement_label(
        {"import_meta": {"period_start": "2026-04-01", "period_end": "2026-04-30"}}
    ) == ("1–30 Apr 2026 statement")
    assert (
        statement_label({"import_meta": {"statement_date": "2026-04-21"}})
        == "21 Apr 2026 statement"
    )
    assert statement_label({"filename": "1234567890_1234567.xlsx"}) == "1234567890_1234567.xlsx"
    assert statement_label({}) == "An earlier statement"
    assert statement_label({"filename": "   "}) == "An earlier statement"
