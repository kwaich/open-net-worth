"""A dedup ref must identify ONE transaction — not one counterparty.

(The companion tests for `migrate_qualify_external_refs` were removed with that one-shot
script on 2026-08-28 — it rewrote refs already stored in the owner's own book, and a fresh
install has none. What remains here is the LIVE minting behaviour, which is what still runs.)

The scar: ``external_ref`` keyed a row on ``ref:{account}:{reference}`` alone, assuming a
printed bank reference identifies a transaction. It often identifies the COUNTERPARTY
instead — a wallet top-up prints the recipient's own identifier (for a mobile-linked wallet
transfer that is a MOBILE NUMBER, e.g. ``81110000``) and a tax payment prints the payer's NATIONAL ID
(e.g. ``S1234567D``), so every such row an account ever issues collided on one id.

Each one after the first was flagged "Already imported — protected by its external reference"
and silently dropped: a June −200.00 top-up suppressed by a July −1.40 one, and a June tax
payment suppressed by July's. Money that never reached the ledger, with the import reporting
success.

Both example values here are SYNTHETIC. Never paste a real one: a counterparty identifier in
this position is personal data (a phone number or a government id), not a transaction ref.
"""

from __future__ import annotations

from backend.services.statements.importer import external_ref

ACCT = "acc_meridian"


def _ref(date: str, amount: float, *, reference: str | None, note: str = "Advice Funds Transfer"):
    return external_ref(ACCT, date, note, amount, False, reference)


def test_the_live_collision_is_gone() -> None:
    """Two wallet top-ups: different months, different amounts, SAME printed reference."""
    june = _ref("2026-06-05", 200.00, reference="81110000")
    july = _ref("2026-07-24", 1.40, reference="81110000")

    assert june != july


def test_same_reference_and_amount_but_a_different_month_still_differs() -> None:
    """The IRAS case: a recurring payment of an identical amount. Amount alone wouldn't have
    told these apart — the date is what does it."""
    june = _ref("2026-06-08", 1200.00, reference="S1234567D")
    july = _ref("2026-07-06", 1200.00, reference="S1234567D")

    assert june != july


def test_the_same_row_still_produces_the_same_ref() -> None:
    """Idempotence is the whole point of the id: re-importing a statement must not duplicate."""
    assert _ref("2026-06-05", 200.00, reference="81110000") == _ref(
        "2026-06-05", 200.00, reference="81110000"
    )


def test_the_reference_still_absorbs_a_reformatted_description() -> None:
    """Why a bank reference is preferred over a content hash at all: a re-export that
    rewords the narration must NOT read as a new transaction."""
    a = _ref("2026-06-05", 200.00, reference="81110000", note="Advice Funds Transfer")
    b = _ref("2026-06-05", 200.00, reference="81110000", note="ADVICE FUNDS TRANSFER (TOP-UP)")

    assert a == b


def test_direction_is_part_of_the_id() -> None:
    """A +100 and a −100 on one day are two transactions, not one."""
    out = external_ref(ACCT, "2026-06-05", "x", 100.0, False, "R1")
    inn = external_ref(ACCT, "2026-06-05", "x", 100.0, True, "R1")
    assert out != inn


def test_a_row_with_no_reference_is_unchanged() -> None:
    """The content-hash branch already folded date and amount in — it was never affected."""
    ref = _ref("2026-06-05", 200.00, reference=None)
    assert not ref.startswith("ref:") and len(ref) == 24
