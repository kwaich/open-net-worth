"""``matching._classify_settled_drift`` — the banded WEAK tier (RFC 2026-07-29 §D2).

An FX card charge settles 1–3% off the booking-time estimate, which is outside every ordinary
tolerance in the engine. That evidence used to live in a SECOND matcher
(``ledger_diff._rank_twin_pairs``) with its own tolerances, its own tokenizer, its own 1:1 pass
and NONE of the engine's invariants — and, since ``decisions._twin_eligible_ids``, its own
commit-time write authority. §D2 absorbed it as a tier inside ``_classify``.

This file carries forward every behaviour of the retired matcher that survived the move, and
pins the two that deliberately did NOT. The rest of the closure argument (kind, direction,
account) is in ``test_twin_invariant_closure.py``; the multi-row assignment is in
``test_golden_books.py::test_estimate_drift_book``.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import matching
from backend.services.matching import MatchPolicy

_SGD = {"SGD": 1.0}
_ID = [0]


@pytest.fixture(autouse=True)
def isolate_stores(monkeypatch, db_isolated):
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    txns = TransactionStore()
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    return accounts, txns


def expense(amt, date, desc, *, acct="card", ccy="SGD", source="import", **over):
    _ID[0] += 1
    row = {
        "kind": "expense",
        "date": date,
        "description": desc,
        "category": None,
        "from_account_id": acct,
        "from_amount": amt,
        "from_currency": ccy,
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "external_refs": [{"source": None, "ref": None}],
        "source": source,
        "id": f"sd-{_ID[0]}",
    }
    row.update(over)
    return row


def _drift(incoming, candidate, policy=None):
    """The tier as ``_apply_batch_match`` reaches it — through ``_classify``, never directly,
    so every gate above it is part of what is being asserted."""
    kw = {} if policy is None else {"policy": policy}
    return matching._classify(incoming, candidate, _SGD, weak_tier=True, **kw)


# ══ The tier's own gates ═══════════════════════════════════════════════════════════════


def test_the_grab_shape_pairs_in_the_weak_band():
    """The shape the tier exists for (owner, live): an agent booked the ride from its email
    receipt at the estimated −S$13.71; the statement settled it at −S$14.07, 2.6% apart. Outside
    ``AMOUNT_REL_TOL``, inside ``TWIN_SETTLED_DRIFT_TOL``, two shared wording tokens."""
    booked = expense(13.71, "2026-06-15", "Grab ride to office", source="ai")
    settled = expense(14.07, "2026-06-15", "GRAB* RIDE SINGAPORE")

    cand = _drift(settled, booked)

    assert cand is not None
    assert cand.klass is matching.MatchClass.NEAR_DUPLICATE
    assert cand.weak is True
    assert matching.SCORE_WEAK <= cand.score < matching.SCORE_FENCE
    assert cand.reasons == [
        "same account, settled 2.6% off, same day — wording overlaps (grab, ride)"
    ]
    # ...and nothing at all without the keyword.
    assert matching._classify(settled, booked, _SGD) is None


def test_drift_past_the_tolerance_never_pairs():
    """Outside ``TWIN_SETTLED_DRIFT_TOL`` there is no tier at all — the band is a floor on
    evidence, not a licence to pair anything with a shared word."""
    booked = expense(13.00, "2026-06-15", "Grab ride to office", source="ai")
    settled = expense(14.07, "2026-06-15", "GRAB* RIDE SINGAPORE")  # 7.6% apart

    assert _drift(settled, booked) is None


def test_drift_tier_requires_shared_wording():
    """Carried forward from the retired matcher (its Fix 2(a)): a non-exact pairing with ZERO
    shared tokens is noise, not evidence. A same-day in-band amount alone must never be read as
    two different merchants being one purchase."""
    booked = expense(14.16, "2026-06-19", "Random Grocery Run", source="agent")
    settled = expense(14.63, "2026-06-19", "ANTHROPIC SAN FRANCISCO")

    assert _drift(settled, booked) is None


def test_a_three_letter_brand_is_wording():
    """Carried forward (its FIX 3): KFC/BHG-class books — the brand IS the wording and it is
    three letters. The engine's ``desc_tokens`` has no length floor at all, so these books are
    covered without the retired tokenizer's ≥3 rule."""
    booked = expense(14.50, "2026-06-18", "KFC dinner", source="ai")
    settled = expense(14.05, "2026-06-18", "KFC BUGIS JUNCTION SG")

    cand = _drift(settled, booked)

    assert cand is not None and cand.weak is True


def test_a_five_digit_reference_is_not_wording():
    """``desc_tokens`` excludes digit-dense tokens — a bank trace number printed on both sides is
    identity-grade signal that belongs to the shared-reference tier, not word overlap. Two
    unrelated merchants that happen to share a long numeric run must not pair here."""
    booked = expense(14.50, "2026-06-18", "Grocery run 998877665", source="ai")
    settled = expense(14.05, "2026-06-18", "BAKERY CORNER 998877665")

    assert _drift(settled, booked) is None


def test_gap_beyond_one_day_never_pairs():
    """The retired matcher's own ``gap > 1`` rejection, kept — a drifted amount two days out is
    an ordinary recurring charge far more often than it is a re-settled one."""
    booked = expense(13.71, "2026-06-13", "Grab ride to office", source="ai")
    settled = expense(14.07, "2026-06-15", "GRAB* RIDE SINGAPORE")

    assert _drift(settled, booked) is None
    assert _drift(expense(14.07, "2026-06-14", "GRAB* RIDE SINGAPORE"), booked) is not None


def test_posted_date_allowance_is_deliberately_not_carried_over():
    """RFC R7/Q2, pinned so the drop stays a CHOICE. The retired matcher measured the gap as
    ``min(gap_to_date, gap_to_posted_date)``; the engine reads ``date`` only. A row whose txn
    date is 2 days out but whose POSTED date is 1 day out therefore no longer pairs.

    Carrying it over would mean teaching ``matching`` about ``posted_date`` — a statement-row
    concept the engine's transaction dicts do not carry — for one tier only. If that allowance is
    ever wanted back, this test is where it lands."""
    booked = expense(42.00, "2026-06-16", "Kopitiam lunch", source="ai")
    # The engine sees only `date`; a draft's posted_date is not part of a transaction.
    settled = expense(42.30, "2026-06-18", "KOPITIAM TAMPINES")

    assert _drift(settled, booked) is None


# ══ The band is what keeps the tier safe ═══════════════════════════════════════════════


def test_a_weak_candidate_can_never_promote_or_auto_merge(isolate_stores):
    """The clamp into ``[SCORE_WEAK, SCORE_FENCE)`` is the whole isolation argument: every gate
    that authorizes anything tests the fence or strong threshold, so a clamped candidate cannot
    reach one — even inside a batch claiming a complete window over the right accounts, which is
    exactly what books-close promotion needs."""
    from tests.conftest import seed_stub_accounts

    _, txns = isolate_stores
    seed_stub_accounts([{"from_account_id": "card"}])
    booked = txns.append(expense(13.71, "2026-06-15", "Grab ride to office", source="ai"))
    rows = [expense(14.07, "2026-06-15", "GRAB* RIDE SINGAPORE")]
    ctx = matching.BatchContext(
        complete=True, window=("2026-06-01", "2026-06-30"), account_ids=frozenset({"card"})
    )

    result = matching.match_batch(rows, context=ctx, weak_tier=True)

    assert result[0].band == "weak"
    assert result[0].assigned.id == booked["id"]
    assert result[0].promoted is False
    assert matching.combine(matching.MatchResult("review", result[0].candidates), "auto") != "merge"


def test_check_never_sees_the_tier(isolate_stores):
    """``check()`` — the single-row entry point every OTHER ingest surface calls
    (``import_anomalies``, connector sync, ``bulk_add_transactions``, the FLOOR choke) — takes no
    ``weak_tier`` keyword at all, so no policy and no argument shuffle can reach the tier through
    it. Pinned as a signature contract, not just a behaviour."""
    import inspect

    from tests.conftest import seed_stub_accounts

    _, txns = isolate_stores
    seed_stub_accounts([{"from_account_id": "card"}])
    txns.append(expense(13.71, "2026-06-15", "Grab ride to office", source="ai"))

    assert "weak_tier" not in inspect.signature(matching.check).parameters
    res = matching.check(expense(14.07, "2026-06-15", "GRAB* RIDE SINGAPORE"), rates=_SGD)
    assert res.action == "create"
    assert res.candidates == []


def test_one_ledger_entry_backs_at_most_one_statement_row(isolate_stores):
    """Carried forward: the 1:1 property the retired matcher hand-rolled. Two statement rows both
    reach the same booked entry through the drift tier; global claim assignment gives it to one,
    and the loser falls to nothing rather than double-claiming."""
    from tests.conftest import seed_stub_accounts

    _, txns = isolate_stores
    seed_stub_accounts([{"from_account_id": "card"}])
    booked = txns.append(expense(14.00, "2026-06-15", "Anthropic subscription", source="agent"))
    rows = [
        expense(14.40, "2026-06-15", "ANTHROPIC SAN FRANCISCO"),
        expense(14.42, "2026-06-15", "ANTHROPIC SAN FRANCISCO"),
    ]

    result = matching.match_batch(
        rows, context=matching.BatchContext(complete=False), weak_tier=True
    )

    assigned = [rm.assigned.id if rm.assigned else None for rm in result]
    assert assigned.count(booked["id"]) == 1
    assert assigned.count(None) == 1


def test_the_closer_amount_wins_the_contested_entry(isolate_stores):
    """Carried forward: every drift pairing clamps to the SAME score, so fidelity — the engine's
    own ranking tie-breaker — is what decides, exactly as the retired matcher's own rank tuple
    did. The truer figures win, never input order."""
    from tests.conftest import seed_stub_accounts

    _, txns = isolate_stores
    seed_stub_accounts([{"from_account_id": "card"}])
    booked = txns.append(expense(14.00, "2026-06-15", "Anthropic subscription", source="agent"))
    rows = [
        expense(14.60, "2026-06-15", "ANTHROPIC SAN FRANCISCO"),  # 4.1% off
        expense(14.10, "2026-06-15", "ANTHROPIC SAN FRANCISCO"),  # 0.7% off… but see below
    ]

    result = matching.match_batch(
        rows, context=matching.BatchContext(complete=False), weak_tier=True
    )

    # Row 1 is inside the ordinary 1% near-dup tolerance, so it never reaches the weak tier at
    # all — it takes the entry through F21's wordless tier (review band), and row 0's weak claim
    # loses outright. A weak claim can never outrank a fence-band one: the clamp guarantees it.
    assert result[1].band == "review"
    assert result[1].assigned.id == booked["id"]
    assert result[0].assigned is None


# ══ Interactions with the rest of _classify ════════════════════════════════════════════


def test_the_wider_gate_never_leaks_into_a_transfer_pair(isolate_stores):
    """The tier needs the shared amount gate widened to ``TWIN_SETTLED_DRIFT_TOL`` (5%) to be
    reachable at all, which is wider than ``TRANSFER_FEE_REL_TOL`` (3%). Every other class is
    re-gated at its own tolerance (``class_gate_ok``), so a 4%-apart opposite-direction pair must
    NOT become a transfer pair just because the drift tier is on."""
    from tests.conftest import seed_stub_accounts

    _, txns = isolate_stores
    seed_stub_accounts([{"from_account_id": "cash"}])
    credit = txns.append(
        {
            "kind": "income",
            "date": "2026-06-15",
            "description": "ACH CREDIT RECEIVED",
            "to_account_id": "cash",
            "to_amount": 2000.0,
            "to_currency": "SGD",
            "source": "simplefin",
            "id": "sd-credit",
        }
    )
    debit = expense(2080.0, "2026-06-15", "ACH TRANSFER TO SAVINGS")  # 3.85% apart

    assert matching._classify(debit, credit, _SGD) is None
    assert matching._classify(debit, credit, _SGD, weak_tier=True) is None
    assert credit["id"] == "sd-credit"


def test_the_wider_gate_never_leaks_into_a_leg_echo():
    """Same argument for F06: a single-leg row 4% off a booked transfer's leg is not an echo, and
    turning the drift tier on must not make it one."""
    transfer = {
        "kind": "transfer",
        "date": "2026-06-15",
        "description": "Card payment",
        "from_account_id": "card",
        "from_amount": 500.0,
        "from_currency": "SGD",
        "to_account_id": "cc",
        "to_amount": 500.0,
        "to_currency": "SGD",
        "source": "manual",
        "id": "sd-transfer",
    }
    single = expense(520.0, "2026-06-15", "PAYMENT TO CARD")  # 3.85% apart

    assert matching._classify(single, transfer, _SGD) is None
    assert matching._classify(single, transfer, _SGD, weak_tier=True) is None


def test_a_policy_can_still_tighten_the_tier():
    """The tier obeys ``MatchPolicy`` like every other class: the owner's "no drift in date" and
    "this source is never more than 1% off" narrow it, and can never widen it."""
    booked = expense(13.71, "2026-06-14", "Grab ride to office", source="ai")
    settled = expense(14.07, "2026-06-15", "GRAB* RIDE SINGAPORE")

    assert _drift(settled, booked) is not None
    assert _drift(settled, booked, MatchPolicy(max_date_drift_days=0)) is None
    assert _drift(settled, booked, MatchPolicy(amount_tolerance_pct=0.01)) is None
    assert _drift(settled, booked, MatchPolicy(drift_matching=False)) is None
    # ...and asking for MORE than the tier's own tolerance changes nothing.
    wide = _drift(
        expense(13.00, "2026-06-15", "GRAB* RIDE SINGAPORE"),
        booked,
        MatchPolicy(amount_tolerance_pct=0.99),
    )
    assert wide is None


def test_an_adjustment_row_is_still_untouchable():
    """F20 through the tier: the Starting-Balance opening transaction is a balance correction, not
    a movement, and a 3%-off coincidence must not make it a duplicate of a charge. Inherited for
    free — the gate is above the tier in ``_classify``, which is the point of putting it there."""
    opening = {
        "kind": "adjustment",
        "date": "2026-06-15",
        "description": "Starting Balance",
        "to_account_id": "card",
        "to_amount": 14.07,
        "to_currency": "SGD",
        "source": "opening",
        "id": "sd-opening",
    }
    settled = expense(13.71, "2026-06-15", "Starting balance top up")

    assert matching._classify(settled, opening, _SGD, weak_tier=True) is None


def test_same_feed_rows_are_still_distinct_charges():
    """F05 through the tier: two rows of the SAME import batch are two real charges, however
    close their amounts. Inherited from ``_classify``'s vetoes, above the tier."""
    a = expense(13.71, "2026-06-15", "Grab ride", batch_id="b1")
    b = expense(14.07, "2026-06-15", "Grab ride", batch_id="b1")

    assert matching._classify(b, a, _SGD, weak_tier=True) is None


# ══ A precision loss, named ════════════════════════════════════════════════════════════


def test_stopword_only_overlap_now_forms_a_weak_pair_known_regression():
    """KNOWN PRECISION LOSS, pinned deliberately — do not read this as intended behaviour.

    The retired matcher stripped a 27-word stoplist ("the", "and", "pte", "usd"…) so that a
    drift pairing sharing ONLY grammar or a currency code was still treated as wordless. The
    engine's ``desc_tokens`` has no stoplist, so "THE BAKERY CORNER" and "The gym session" now
    share ``the`` and the pairing forms.

    Re-introducing the stoplist inside the tier would rebuild the second tokenizer §D2 exists to
    delete, and adding it to ``desc_tokens`` itself moves scoring on EVERY surface and every
    golden book — the same shape of change as the non-Latin normalization (RFC R4), and it wants
    the same treatment: its own change, its own ADR, its own re-pin.

    What contains it meanwhile is the band. This is a WEAK candidate: it cannot promote, cannot
    auto-merge, cannot reach ``check()``'s review verdict, and at the import surface it renders
    as an offer the owner accepts or dismisses. The old matcher's output was, by contrast, a
    commit-time acceptance set (``_twin_eligible_ids``) — so the pairing is broader here and
    strictly less powerful."""
    booked = expense(14.50, "2026-06-18", "The gym session", source="ai")
    settled = expense(14.05, "2026-06-18", "THE BAKERY CORNER")

    cand = _drift(settled, booked)

    assert cand is not None and cand.weak is True, (
        "If a merchant-token notion landed, update this pin and docs/INVARIANTS.md rather than "
        "deleting it — the precision question is real, the band is what makes it survivable."
    )
