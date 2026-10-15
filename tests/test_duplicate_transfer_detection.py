"""A transfer already in the book cannot be booked a second time.

Root-caused from a repro (2026-08-09): an email-ingested "Meridian Bank Mastercard World (Ref
ending 4421)" landed in the ledger beside a Lucius-logged "Meridian Bank CC bill payment (paying
down card balance)" — same date, same S$640.00, same from-leg, same to-leg, both `source: ai`.
Two rows, one real payment, net worth understated by S$640.00.

The write choke DOES refuse semantic duplicates (`matching.blocking_duplicate` above the insert),
so this was not a threshold miss — running the engine against the pair returned ZERO candidates.
No match class covered it:

  * TRANSFER_PAIR      wants one single-leg debit + one single-leg credit; two two-leg transfers
                       are neither.
  * TRANSFER_LEG_ECHO  wants a single-leg row echoing one leg of a transfer.
  * NEAR_DUPLICATE     was gated `kind in ("expense", "income")` — F14a, whose stated purpose is
                       CROSS-kind protection (an expense is never a transfer's duplicate). The
                       blanket exclusion also barred transfer↔transfer, which F14a never intended.

The only remaining guard was L0's exact `(source, ref)` pair, and the hand-logged row carried no
ref at all — so any feed with its own ref could re-book the same movement forever.
"""

from __future__ import annotations

from backend.services import matching

ROUTES = frozenset({frozenset(("acc_bank", "acc_card"))})

BASE = dict(
    date="2026-08-08",
    from_amount=640.00,
    to_amount=640.00,
    from_currency="SGD",
    to_currency="SGD",
)


def _transfer(**over) -> dict:
    return {
        **BASE,
        "kind": "transfer",
        "from_account_id": "acc_bank",
        "to_account_id": "acc_card",
        "description": "Meridian Bank CC bill payment (paying down card balance)",
        **over,
    }


def _klass(a: dict, b: dict):
    return matching._classify(a, b, None, ROUTES)


# ── the live pair ──────────────────────────────────────────────────────────────────────


def test_the_live_pair_is_now_a_duplicate() -> None:
    """The two rows exactly as they sit in the live ledger — identical but for the wording
    each feed chose. The ROUTE is the identity evidence; neither description mentions the other."""
    hand_logged = _transfer(source="ai")
    email_ingested = _transfer(
        source="ai",
        description="Meridian Bank Mastercard World (Ref ending 4421)",
        batch_id="imp_d8d100e6ca31",
    )

    cand = _klass(email_ingested, hand_logged)

    assert cand is not None, "the engine saw no candidate at all — this is the original bug"
    assert cand.klass is matching.MatchClass.NEAR_DUPLICATE
    assert cand.score >= matching.SCORE_STRONG


def test_the_write_choke_actually_refuses_it() -> None:
    """The classification is only half the fix: `blocking_duplicate` is what turns a candidate
    into a REFUSED write, and it demands both a duplicate class and STRONG evidence."""
    hand_logged = _transfer(source="ai")
    email_ingested = _transfer(
        source="ai", description="Meridian Bank Mastercard World", batch_id="b1"
    )

    result = matching.check(email_ingested, profile=matching.FULL, candidates=[hand_logged])

    assert matching.blocking_duplicate(result) is not None


# ── what must still be allowed ─────────────────────────────────────────────────────────


def test_a_round_trip_is_not_a_duplicate() -> None:
    """A→B and B→A move money back; collapsing them would erase a real leg of a real round trip."""
    assert (
        _klass(_transfer(), _transfer(from_account_id="acc_card", to_account_id="acc_bank")) is None
    )


def test_a_different_destination_is_not_a_duplicate() -> None:
    """Two payments of the same amount out of the same account to DIFFERENT accounts are two
    payments. A shared source account is not identity."""
    assert _klass(_transfer(), _transfer(to_account_id="acc_other")) is None


def test_a_distant_pair_is_review_not_a_refusal() -> None:
    """Same route and amount but days apart is weaker evidence, not stronger: two identical
    monthly card payments are ordinary. Capped below STRONG so the queue ASKS rather than the
    write choke refusing — mirroring F14b for near-duplicates."""
    cand = _klass(_transfer(date="2026-08-14"), _transfer())

    assert cand is not None
    assert cand.score < matching.SCORE_STRONG


def test_a_differing_amount_is_not_a_duplicate() -> None:
    """The route alone never carries a match — the amount gate still runs first."""
    assert _klass(_transfer(), _transfer(from_amount=1234.00, to_amount=1234.00)) is None


# ── F14a's actual purpose is preserved ─────────────────────────────────────────────────


def test_an_expense_is_still_never_a_transfers_near_duplicate() -> None:
    """F14a exists to stop CROSS-kind pairing. Relaxing it for transfer↔transfer must not let an
    ordinary expense row near-duplicate a transfer: a single-leg row against a two-leg transfer
    is TRANSFER_LEG_ECHO's job, and that class caps itself at review."""
    expense = dict(
        date="2026-08-08",
        kind="expense",
        from_account_id="acc_bank",
        from_amount=640.00,
        from_currency="SGD",
        description="Meridian Bank CC bill payment",
    )

    cand = _klass(expense, _transfer())

    assert cand is None or cand.klass is not matching.MatchClass.NEAR_DUPLICATE


def test_an_adjustment_is_still_excluded() -> None:
    """F20 sits above the kind gate and is untouched — a Starting Balance is not a movement."""
    adj = _transfer(kind="adjustment")
    assert _klass(adj, _transfer()) is None
    assert _klass(_transfer(), adj) is None
