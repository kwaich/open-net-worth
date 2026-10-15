"""``MatchPolicy`` — the per-call, monotone-TIGHTENING override of the engine's constants.

RFC ``.claude/rfcs/2026-07-29-one-match-engine-instructions-as-policy.md`` §D1, Phase 1
("MatchPolicy plumbing, default-only"). The policy is what turns free-form owner instructions
("no drift in date") into arithmetic the engine performs, instead of prose an LLM re-derives
per row per import.

The whole safety argument rests on ONE property, so it is what this file tests hardest:

    every knob is monotone-TIGHTENING — ``max_date_drift_days`` applied as
    ``min(policy_value, class_cap)`` and ``amount_tolerance_pct`` as ``min(policy_value,
    class_tol)``, so a policy can never LOOSEN the engine.

That is what lets one surface derive a policy from prose without moving the safety line of
``import_anomalies``, ``match_review``, ``bulk_add_transactions``, connector sync or the FLOOR
choke — pinned byte-identical in ``test_char_statement_match_surface.py``.

The engine's ONE loosening switch, ``weak_tier``, is deliberately NOT on this dataclass — it is
a call-site keyword on ``match_batch``/``_classify`` (RFC §D2 + security review 2026-07-29 #4).
A policy is DATA: derived from owner prose, cached on a format profile, and reachable from skill
metadata in Phase 3. A loosening knob living there would be steerable by an injected
instruction; as a keyword it is code, and no data path can reach it. Pinned below.

Phase 1 shipped NO caller that passes a non-default policy; ``MatchPolicy()`` is exactly today's
engine.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import matching
from backend.services.matching import MatchPolicy

_SGD = {"SGD": 1.0}


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


_ID = [0]


def _next_id():
    _ID[0] += 1
    return f"mp-row-{_ID[0]}"


def expense(acct, amt, date, desc, *, ccy="SGD", source="import", **over):
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
        "id": _next_id(),
    }
    row.update(over)
    return row


def income(acct, amt, date, desc, *, ccy="SGD", source="import"):
    return {
        "kind": "income",
        "date": date,
        "description": desc,
        "category": None,
        "to_account_id": acct,
        "to_amount": amt,
        "to_currency": ccy,
        "from_account_id": None,
        "from_amount": None,
        "from_currency": None,
        "external_refs": [{"source": None, "ref": None}],
        "source": source,
        "id": _next_id(),
    }


def _classify(incoming, candidate, policy=None):
    kw = {} if policy is None else {"policy": policy}
    return matching._classify(incoming, candidate, _SGD, **kw)


# ══ The two helpers ARE the rule — test them directly ══════════════════════════════════


@pytest.mark.parametrize(
    "policy_value,class_cap,expected",
    [
        (None, 3, 3),  # unset → the class keeps its own cap
        (0, 3, 0),  # tighter → wins
        (1, 3, 1),
        (3, 3, 3),  # equal → no change
        (99, 3, 3),  # LOOSER → refused; the class cap still wins
    ],
)
def test_date_cap_is_monotone_tightening(policy_value, class_cap, expected):
    assert MatchPolicy(max_date_drift_days=policy_value).date_cap(class_cap) == expected


@pytest.mark.parametrize(
    "policy_value,class_tol,expected",
    [
        (None, 0.03, 0.03),
        (0.0, 0.03, 0.0),
        (0.01, 0.03, 0.01),
        (0.03, 0.03, 0.03),
        (0.99, 0.03, 0.03),  # LOOSER → refused
    ],
)
def test_amount_tol_is_monotone_tightening(policy_value, class_tol, expected):
    assert MatchPolicy(amount_tolerance_pct=policy_value).amount_tol(class_tol) == expected


def test_default_policy_is_a_no_op_over_every_engine_constant():
    """``MatchPolicy()`` must return each constant untouched — the Phase 1 contract that every
    characterization pin rests on."""
    p = MatchPolicy()
    for cap in (
        matching.FETCH_WINDOW_DAYS,
        matching.TRANSFER_PAIR_MAX_GAP_DAYS,
        matching.WORDLESS_MAX_GAP_DAYS,
    ):
        assert p.date_cap(cap) == cap
    for tol in (
        matching.AMOUNT_REL_TOL,
        matching.FX_AMOUNT_REL_TOL,
        matching.TRANSFER_FEE_REL_TOL,
        matching.EXACT_AMOUNT_REL_TOL,
    ):
        assert p.amount_tol(tol) == tol


def test_policy_is_frozen_and_hashable():
    """Frozen so it can never be mutated mid-pass by one classifier and read by the next —
    the "passed explicitly, never a global" property in dataclass form."""
    p = MatchPolicy()
    with pytest.raises(Exception):
        p.max_date_drift_days = 0  # type: ignore[misc]
    assert hash(p) == hash(MatchPolicy())


# ══ max_date_drift_days — strictly shrinks, can never widen ════════════════════════════


def _one_day_apart_pair():
    """A near-duplicate one day apart: matches under the default policy."""
    booked = expense("meridian", 25.50, "2026-06-01", "GRAB SG", source="email")
    incoming = expense("meridian", 25.50, "2026-06-02", "GRAB* SG RIDE")
    return incoming, booked


def test_zero_date_drift_strictly_shrinks_the_candidate_set():
    incoming, booked = _one_day_apart_pair()
    assert _classify(incoming, booked) is not None  # default: a match

    assert _classify(incoming, booked, MatchPolicy(max_date_drift_days=0)) is None


def test_zero_date_drift_keeps_a_same_day_pair():
    """Tightening to 0 does not break same-day matching — it removes only the drift."""
    booked = expense("meridian", 25.50, "2026-06-01", "GRAB SG", source="email")
    incoming = expense("meridian", 25.50, "2026-06-01", "GRAB* SG RIDE")

    assert _classify(incoming, booked, MatchPolicy(max_date_drift_days=0)) is not None


def test_a_loosening_date_policy_cannot_widen_past_the_class_caps():
    """``max_date_drift_days=99`` must change NOTHING: every class cap still applies, so a pair
    outside FETCH_WINDOW_DAYS stays unmatched and every in-window verdict is byte-identical."""
    booked = expense("meridian", 25.50, "2026-06-01", "GRAB SG", source="email")
    far = expense("meridian", 25.50, "2026-06-20", "GRAB* SG RIDE")  # 19 days — past the window
    near = expense("meridian", 25.50, "2026-06-02", "GRAB* SG RIDE")
    loose = MatchPolicy(max_date_drift_days=99)

    assert _classify(far, booked, loose) is None

    default_hit = _classify(near, booked)
    loose_hit = _classify(near, booked, loose)
    assert (loose_hit.klass, loose_hit.score, loose_hit.reasons) == (
        default_hit.klass,
        default_hit.score,
        default_hit.reasons,
    )


def test_date_policy_tightens_transfer_pair_formation():
    """The Friday→Monday ACH shape (3 days, TRANSFER_PAIR_MAX_GAP_DAYS) dies at a 1-day cap."""
    credit = income("harbor", 2000.0, "2026-06-08", "ACH CREDIT RECEIVED", ccy="USD")
    debit = expense("anchor", 2000.0, "2026-06-05", "ACH TRANSFER TO SAVINGS", ccy="USD")

    assert _classify(debit, credit).klass is matching.MatchClass.TRANSFER_PAIR
    assert _classify(debit, credit, MatchPolicy(max_date_drift_days=1)) is None


def test_date_policy_tightens_the_wordless_gap_tier():
    """F21's adjacent-day wordless tier (WORDLESS_MAX_GAP_DAYS=1) closes at 0."""
    booked = expense("meridian", 8.00, "2026-06-01", "Coffee run", source="ai")
    incoming = expense("meridian", 8.00, "2026-06-02", "SBUX 4471 ORCHARD")

    assert _classify(incoming, booked).klass is matching.MatchClass.NEAR_DUPLICATE
    assert _classify(incoming, booked, MatchPolicy(max_date_drift_days=0)) is None


# ══ amount_tolerance_pct — strictly shrinks, can never widen ═══════════════════════════


def test_zero_amount_tolerance_strictly_shrinks_the_candidate_set():
    """A 0.5%-off same-day pair matches by default (inside AMOUNT_REL_TOL) and is refused at 0."""
    booked = expense("meridian", 100.00, "2026-06-01", "GRAB SG", source="email")
    incoming = expense("meridian", 100.40, "2026-06-01", "GRAB* SG RIDE")

    assert _classify(incoming, booked) is not None
    assert _classify(incoming, booked, MatchPolicy(amount_tolerance_pct=0.0)) is None


def test_zero_amount_tolerance_keeps_an_exact_pair():
    booked = expense("meridian", 100.00, "2026-06-01", "GRAB SG", source="email")
    incoming = expense("meridian", 100.00, "2026-06-01", "GRAB* SG RIDE")

    assert _classify(incoming, booked, MatchPolicy(amount_tolerance_pct=0.0)) is not None


def test_a_loosening_amount_policy_cannot_widen_past_the_class_tolerances():
    """``amount_tolerance_pct=0.99`` must change NOTHING — a 20%-off pair stays unmatched."""
    booked = expense("meridian", 100.00, "2026-06-01", "GRAB SG", source="email")
    way_off = expense("meridian", 120.00, "2026-06-01", "GRAB* SG RIDE")
    close = expense("meridian", 100.40, "2026-06-01", "GRAB* SG RIDE")
    loose = MatchPolicy(amount_tolerance_pct=0.99)

    assert _classify(way_off, booked, loose) is None

    default_hit = _classify(close, booked)
    loose_hit = _classify(close, booked, loose)
    assert (loose_hit.klass, loose_hit.score, loose_hit.reasons) == (
        default_hit.klass,
        default_hit.score,
        default_hit.reasons,
    )


def test_amount_policy_tightens_the_fee_bearing_transfer_band():
    """A 2%-off same-currency transfer pair rides TRANSFER_FEE_REL_TOL (3%). A 1% policy
    refuses it entirely rather than merely capping its score."""
    credit = income("compass", 980.0, "2026-06-02", "TRANSFER IN")
    debit = expense("meridian", 1000.0, "2026-06-01", "TRANSFER OUT")

    assert _classify(debit, credit).klass is matching.MatchClass.TRANSFER_PAIR
    assert _classify(debit, credit, MatchPolicy(amount_tolerance_pct=0.01)) is None


# ══ currency_strict ════════════════════════════════════════════════════════════════════


def test_currency_strict_refuses_a_cross_currency_pair_even_with_the_f22_fingerprint():
    """F22's exact original-foreign-amount fingerprint normally survives a missing FX rate.
    ``currency_strict`` is stricter still: no cross-currency pairing at all."""
    booked = expense(
        "meridian",
        60.36,
        "2026-06-01",
        "PHO BO",
        source="ai",
        original_amount=1201200.0,
        original_currency="VND",
    )
    incoming = expense("vcb", 1201200.0, "2026-06-01", "PHO BO QUAN 2", ccy="VND")

    assert _classify(incoming, booked, MatchPolicy(currency_strict=True)) is None


def test_currency_strict_leaves_same_currency_pairs_untouched():
    booked = expense("meridian", 25.50, "2026-06-01", "GRAB SG", source="email")
    incoming = expense("meridian", 25.50, "2026-06-01", "GRAB* SG RIDE")

    strict = _classify(incoming, booked, MatchPolicy(currency_strict=True))
    default = _classify(incoming, booked)
    assert (strict.klass, strict.score, strict.reasons) == (
        default.klass,
        default.score,
        default.reasons,
    )


# ══ blocked_account_ids ════════════════════════════════════════════════════════════════


def test_blocked_account_refuses_any_pair_touching_it():
    booked = expense("meridian", 25.50, "2026-06-01", "GRAB SG", source="email")
    incoming = expense("meridian", 25.50, "2026-06-01", "GRAB* SG RIDE")

    assert (
        _classify(incoming, booked, MatchPolicy(blocked_account_ids=frozenset({"meridian"})))
        is None
    )


def test_blocking_an_unrelated_account_changes_nothing():
    booked = expense("meridian", 25.50, "2026-06-01", "GRAB SG", source="email")
    incoming = expense("meridian", 25.50, "2026-06-01", "GRAB* SG RIDE")

    assert (
        _classify(incoming, booked, MatchPolicy(blocked_account_ids=frozenset({"other"})))
        is not None
    )


# ══ weak_tier is a call-site keyword, never a policy field ═════════════════════════════


def test_weak_tier_is_not_a_policy_field():
    """BLOCKING (security review 2026-07-29 #4). ``MatchPolicy`` is derived from owner prose and
    (Phase 3) from skill metadata an AI can write with no confirmation, so "unknown keys are
    dropped" is no defence: ``weak_tier`` would be a KNOWN key. Keeping the engine's one
    loosening switch off the dataclass entirely is what makes it unreachable from any data path.

    This test fails the moment someone adds it back — including via a Phase 3 extractor that
    "just passes the model's dict through"."""
    fields = set(MatchPolicy.__dataclass_fields__)
    assert "weak_tier" not in fields
    assert fields == {
        "max_date_drift_days",
        "amount_tolerance_pct",
        "currency_strict",
        "drift_matching",
        "blocked_account_ids",
    }
    with pytest.raises(TypeError):
        MatchPolicy(weak_tier=True)  # type: ignore[call-arg]


def test_the_weak_tier_keyword_defaults_off_for_every_caller():
    """The settled-drift shape (2.6% apart) is outside every ordinary tolerance. It stays
    invisible unless a caller explicitly asks for the tier — a policy cannot turn it on, and no
    caller gets it by accident."""
    booked = expense("meridian", 13.71, "2026-06-01", "Grab ride to office", source="ai")
    settled = expense("meridian", 14.07, "2026-06-01", "GRAB* RIDE SINGAPORE")

    assert _classify(settled, booked) is None
    assert _classify(settled, booked, MatchPolicy(max_date_drift_days=0)) is None
    assert matching.check(settled, rates=_SGD, candidates=[booked]).candidates == []

    on = matching._classify(settled, booked, _SGD, weak_tier=True)
    assert on is not None and on.weak is True


# ══ drift_matching subtracts the tier it names, and is inert without it ════════════════


def test_drift_matching_false_switches_the_weak_tier_off():
    """``drift_matching=False`` is the deterministic form of "this source never has
    drift-matched duplicates". Tightening-only by construction: it can subtract the settled-drift
    tier, never add one."""
    booked = expense("meridian", 13.71, "2026-06-01", "Grab ride to office", source="ai")
    settled = expense("meridian", 14.07, "2026-06-01", "GRAB* RIDE SINGAPORE")

    assert matching._classify(settled, booked, _SGD, weak_tier=True) is not None
    assert (
        matching._classify(
            settled, booked, _SGD, policy=MatchPolicy(drift_matching=False), weak_tier=True
        )
        is None
    )


def test_drift_matching_is_inert_for_a_caller_that_never_opted_in():
    """Without ``weak_tier`` there is no drift tier for it to switch off, so flipping it must not
    move a single verdict on any other tier."""
    booked = expense("meridian", 13.71, "2026-06-01", "Grab ride to office", source="ai")
    exact = expense("meridian", 13.71, "2026-06-01", "GRAB* RIDE SINGAPORE")

    on = _classify(exact, booked, MatchPolicy(drift_matching=False))
    off = _classify(exact, booked)
    assert (on.klass, on.score, on.reasons) == (off.klass, off.score, off.reasons)


# ══ End to end through the public entry points ═════════════════════════════════════════


def test_check_accepts_a_policy_and_tightens_with_it():
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "meridian"}])
    booked = ledger_service.transactions.append(
        expense("meridian", 25.50, "2026-06-01", "GRAB SG", source="email")
    )
    incoming = expense("meridian", 25.50, "2026-06-02", "GRAB* SG RIDE")

    assert matching.check(incoming, rates=_SGD).canonical_id == booked["id"]
    tight = matching.check(incoming, rates=_SGD, policy=MatchPolicy(max_date_drift_days=0))
    assert tight.action == "create"
    assert tight.candidates == []


def test_check_l0_floor_is_unreachable_by_any_policy():
    """The exact-external-ref floor runs before L2 and reads no policy — the one gate a
    mis-derived policy must never be able to switch off."""
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "meridian"}])
    booked = ledger_service.transactions.append(
        expense(
            "meridian",
            25.50,
            "2026-06-01",
            "GRAB SG",
            source="email",
            external_refs=[{"source": "email", "ref": "r1"}],
        )
    )
    incoming = expense(
        "meridian",
        25.50,
        "2026-06-01",
        "GRAB SG",
        external_refs=[{"source": "email", "ref": "r1"}],
    )
    airtight = MatchPolicy(
        max_date_drift_days=0,
        amount_tolerance_pct=0.0,
        currency_strict=True,
        blocked_account_ids=frozenset({"meridian"}),
    )

    res = matching.check(incoming, rates=_SGD, policy=airtight)
    assert res.action == "noop"
    assert res.canonical_id == booked["id"]


def test_match_batch_accepts_a_policy_and_tightens_with_it():
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "meridian"}])
    booked = ledger_service.transactions.append(
        expense("meridian", 25.50, "2026-06-01", "GRAB SG", source="email")
    )
    rows = [expense("meridian", 25.50, "2026-06-02", "GRAB* SG RIDE")]
    ctx = matching.BatchContext(complete=False)

    default = matching.match_batch(rows, context=ctx, rates=_SGD)
    assert [(rm.band, rm.assigned.id) for rm in default] == [("strong", booked["id"])]

    tight = matching.match_batch(
        rows, context=ctx, rates=_SGD, policy=MatchPolicy(max_date_drift_days=0)
    )
    assert [(rm.band, rm.assigned, rm.candidates) for rm in tight] == [("none", None, [])]


def test_match_batch_is_deterministic_under_one_policy():
    """Same rows + same policy → the same assignment, every time (no randomness, no time read)."""
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "meridian"}])
    ledger_service.transactions.append(
        expense("meridian", 1000.0, "2026-06-07", "Konbini run", source="ai", ccy="JPY")
    )
    ledger_service.transactions.append(
        expense("meridian", 1000.0, "2026-06-07", "Konbini run", source="ai", ccy="JPY")
    )
    policy = MatchPolicy(max_date_drift_days=1)
    ctx = matching.BatchContext(complete=False)

    def _run():
        rows = [
            expense("meridian", 1000.0, "2026-06-07", "LAWSON SHIBUYA JP", ccy="JPY"),
            expense("meridian", 1000.0, "2026-06-07", "LAWSON SHIBUYA JP", ccy="JPY"),
        ]
        return [
            (rm.band, rm.assigned.id if rm.assigned else None, rm.promoted)
            for rm in matching.match_batch(rows, context=ctx, rates=_SGD, policy=policy)
        ]

    first = _run()
    assert first == _run() == _run()
