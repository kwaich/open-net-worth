"""
Funding-engine tests (RFC Phase 2) — the correctness invariants that make the
verdict trustworthy. Fully isolated: CommitmentsStore is DB-backed (plan 4a task 3) —
db_isolated gives each test its own fresh, migrated database; FX rates and position values
are injected directly (no network, no live prices).

Run:
    .venv/bin/pytest tests/test_commitments_service.py -v
"""

from __future__ import annotations

import datetime as _datetime

import pytest

from backend.db.commitments_store import CommitmentsStore
from backend.services import cashflow as cashflow_service
from backend.services import commitments as cs

RATES = {"SGD": 1.0, "VND": 0.0000531, "USD": 1.35}  # base SGD; rate = base per unit


class _FixedToday(_datetime.date):
    """A `datetime.date` stand-in whose `.today()` is pinned — installed over cashflow's own
    `date` import so `coverage()`'s `build_projection()` call (which reads the real wall-clock
    `date.today()` for its projection window, independent of `cs._TODAY` below) agrees with
    the SAME "today" this fixture pins for commitments' own day-math. Without this, a
    commitment due-dated relative to the fixture's fixed "today" silently drifts outside
    build_projection's forward window as real calendar time passes it — not a refactor
    regression, just an incomplete time-freeze this fixture already intended (see docstring
    below) but didn't fully deliver."""

    @classmethod
    def today(cls):
        return _datetime.date(2026, 6, 3)


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    # pin base + a stable "today" so month math is deterministic
    monkeypatch.setattr(cs.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(cs, "_TODAY", "2026-06-03")
    monkeypatch.setattr(cashflow_service, "user_today", _FixedToday.today)
    return tmp_path


# ── live_source_value: every type (including asset_sale) uses its own frozen amount ─────
# asset_sale used to be live-priced (qty × current market price, capped at held quantity) —
# changed (owner decision, 2026-07-26): the funding plan must not silently drift with the
# market unless the user intentionally edits it, so asset_sale now stores a real amount+
# currency too (the target the user intends to raise) and is valued IDENTICALLY to a fixed
# source. sell_quantity is informational only — no longer read by this function at all.


def test_live_source_value_asset_sale_uses_its_own_stored_amount_not_a_live_price(isolated):
    v = cs.live_source_value(
        {
            "type": "asset_sale",
            "ref_position_id": "p1",
            "sell_quantity": 1000,
            "amount": 5000,
            "currency": "SGD",
        },
        "SGD",
        RATES,
    )
    assert v == 5000.0
    # currency conversion still applies, exactly like a fixed source
    v2 = cs.live_source_value(
        {
            "type": "asset_sale",
            "ref_position_id": "p1",
            "sell_quantity": 200,
            "amount": 1000,
            "currency": "USD",
        },
        "SGD",
        RATES,
    )
    assert v2 == pytest.approx(1350.0)
    # no stored amount yet (not planned) → 0, same as any other unset source — never inflated
    # by sell_quantity or a live price lookup
    assert (
        cs.live_source_value(
            {"type": "asset_sale", "ref_position_id": "x", "sell_quantity": 1, "amount": None},
            "SGD",
            RATES,
        )
        == 0.0
    )


def test_live_source_value_fixed_converts_currency(isolated):
    v = cs.live_source_value({"type": "loan", "amount": 1000, "currency": "USD"}, "SGD", RATES)
    assert v == pytest.approx(1350.0)


# ── projected_events: the type split + ledger-anchored paid + kind gating ──────


def test_projected_events_split_and_anchoring(isolated):
    store = CommitmentsStore()
    store.create(
        {
            "id": "apt",
            "name": "Apt",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {
                    "id": "i1",
                    "label": "1st",
                    "due_date": "2026-07-10",
                    "amount": 1000,
                },  # unpaid → out
                {
                    "id": "i2",
                    "label": "2nd",
                    "due_date": "2026-08-10",
                    "amount": 2000,
                    "paid_txn_id": "t1",
                },  # paid → skip
            ],
            "funding_plan": [
                {
                    "type": "cash",
                    "label": "cash",
                    "amount": 5000,
                    "currency": "SGD",
                },  # baseline → NO event
                {
                    "type": "loan",
                    "label": "loan",
                    "amount": 3000,
                    "currency": "SGD",
                    "available_date": "2026-07-01",
                },
            ],
        }
    )
    ev = cs.projected_events("SGD", RATES)

    jul = ev.get("2026-07", [])
    out = [e for e in jul if e["direction"] == "out"]
    inflow = [e for e in jul if e["direction"] == "in"]
    assert len(out) == 1 and out[0]["amount_base"] == 1000 and out[0]["status"] == "committed"
    assert (
        len(inflow) == 1 and inflow[0]["amount_base"] == 3000 and inflow[0]["status"] == "funding"
    )
    # the paid installment (Aug) produced no outflow; cash earmark produced no inflow anywhere
    assert "2026-08" not in ev
    assert not any(e.get("label", "").endswith("cash") for evs in ev.values() for e in evs)


def test_status_paid_without_txn_id_is_excluded_everywhere(isolated, monkeypatch):
    # MATH-25: the UI's "Mark paid" action (detailMutations.ts payInstallment) sets
    # status="paid" but never paid_txn_id — only the separate link-transaction path does.
    # Before the fix, `upcoming_events()` alone honored a bare status flag; `projected_events()`
    # and `_plan_fidelity_events()` (via `coverage()`) checked ONLY paid_txn_id, so a
    # "marked paid" installment vanished from Upcoming but stayed a future outflow in the
    # cash-flow projection and the per-commitment coverage verdict forever.
    CommitmentsStore().create(
        {
            "id": "loan1",
            "name": "Car Loan",
            "kind": "loan_repayment",
            "currency": "SGD",
            "installments": [
                {"id": "i1", "label": "unpaid", "due_date": "2026-07-10", "amount": 100},
                {
                    "id": "i2",
                    "label": "marked paid",
                    "due_date": "2026-07-15",
                    "amount": 500,
                    "status": "paid",
                },  # UI "Mark paid" path: status only, no paid_txn_id
                {
                    "id": "i3",
                    "label": "ledger-linked paid",
                    "due_date": "2026-07-20",
                    "amount": 900,
                    "paid_txn_id": "t1",
                },
            ],
        }
    )

    ev = cs.projected_events("SGD", RATES)
    out = [e for e in ev.get("2026-07", []) if e["direction"] == "out"]
    assert [e["amount_base"] for e in out] == [100]

    feed = cs.upcoming_events(days=120, rates=RATES)
    assert [e["label"] for e in feed] == ["unpaid"]

    monkeypatch.setattr("backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: 0.0)
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_income", lambda base, *_a, **_k: 0.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_expenses", lambda base, *_a, **_k: 0.0
    )
    cov = cs.coverage(months=6)
    loan1 = next(c for c in cov["commitments"] if c["id"] == "loan1")
    # only the genuinely unpaid $100 installment should count as an outflow the plan must fund
    assert loan1["shortfall_base"] == pytest.approx(100.0)


def test_projected_events_auto_pay_emits_nothing(isolated):
    CommitmentsStore().create(
        {
            "name": "Tax GIRO",
            "kind": "auto_pay",
            "currency": "SGD",
            "installments": [{"label": "Jan", "due_date": "2026-07-10", "amount": 1000}],
        }
    )
    assert cs.projected_events("SGD", RATES) == {}


# ── earmark reconciliation ──────────────────────────────────────────────────────


def test_earmark_reconciliation_flags_over_commit(isolated, monkeypatch):
    CommitmentsStore().create(
        {
            "name": "Apt",
            "kind": "purchase",
            "currency": "SGD",
            "funding_plan": [{"type": "cash", "label": "e", "amount": 50000, "currency": "SGD"}],
        }
    )
    monkeypatch.setattr(
        "backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: 20000.0
    )
    r = cs.earmark_reconciliation("SGD", RATES)
    assert r["earmarked_base"] == 50000.0 and r["liquid_base"] == 20000.0
    assert r["over_committed"] is True and r["excess_base"] == 30000.0


# ── coverage: cash-only baseline + shortfall verdict ───────────────────────────


def test_coverage_flags_shortfall(isolated, monkeypatch):
    # Big near-term installment, thin cash, no income → must be at risk.
    CommitmentsStore().create(
        {
            "id": "apt",
            "name": "Apt",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {"id": "i1", "label": "1st", "due_date": "2026-07-10", "amount": 100000}
            ],
        }
    )
    monkeypatch.setattr(
        "backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: 5000.0
    )
    # zero recurring baseline so the only mover is the installment
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_income", lambda base, *_a, **_k: 0.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_expenses", lambda base, *_a, **_k: 0.0
    )

    cov = cs.coverage(months=6)
    assert cov["at_risk"] is True
    assert cov["total_shortfall_base"] > 0
    apt = next(c for c in cov["commitments"] if c["id"] == "apt")
    assert apt["at_risk"] is True and apt["shortfall_base"] > 0


def test_amortization_zero_interest(isolated):
    sched, pmt = cs.amortization_schedule(1200, 0, 12, "2026-10-15")
    assert pmt == 100.0 and len(sched) == 12
    assert sched[0]["due_date"] == "2026-10-15" and sched[1]["due_date"] == "2026-11-15"
    assert sum(i["amount"] for i in sched) == pytest.approx(1200.0)


def test_amortization_with_interest_clears_balance(isolated):
    sched, pmt = cs.amortization_schedule(50000, 6.5, 24, "2026-10-15")
    assert pmt > 50000 / 24  # includes interest
    assert len(sched) == 24
    # total paid > principal (interest); last payment clears the loan (sum ≈ principal + interest)
    total = sum(i["amount"] for i in sched)
    assert total > 50000
    assert sched[-1]["due_date"] == "2028-09-15"  # 23 months after start


def test_upcoming_events_flattens_and_sorts(isolated, monkeypatch):
    CommitmentsStore().create(
        {
            "name": "Apt",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {"label": "next", "due_date": "2026-07-10", "amount": 1000},
                {
                    "label": "paid",
                    "due_date": "2026-06-20",
                    "amount": 500,
                    "paid_txn_id": "t1",
                },  # excluded
            ],
            "actions": [
                {
                    "label": "Apply loan",
                    "category": "Pre-arrangement",
                    "due_date": "2026-06-15",
                    "status": "pending",
                },
                {"label": "old done", "due_date": "2026-06-10", "status": "done"},  # excluded
            ],
            "funding_plan": [
                {
                    "type": "loan",
                    "label": "Meridian Bank",
                    "amount": 50000,
                    "currency": "SGD",
                    "available_date": "2026-06-30",
                },
                {
                    "type": "cash",
                    "label": "cash",
                    "amount": 1000,
                    "currency": "SGD",
                },  # not new-money → excluded
            ],
        }
    )
    feed = cs.upcoming_events(days=120, rates=RATES)
    kinds = [(e["date"], e["kind"]) for e in feed]
    # rows must be addressable so the UI can mark an action done / link a payment,
    # and carry the commitment kind so it can pick the honest control per row
    assert all(e["action_id"] for e in feed if e["kind"] == "action")
    assert all(e["installment_id"] for e in feed if e["kind"] == "installment")
    assert all(e["commitment_kind"] == "purchase" for e in feed)
    assert kinds == [
        ("2026-06-15", "action"),
        ("2026-06-30", "funding"),
        ("2026-07-10", "installment"),
    ]
    assert all(e["label"] not in ("paid", "old done", "cash") for e in feed)


def test_coverage_covered_when_funded(isolated, monkeypatch):
    CommitmentsStore().create(
        {
            "id": "apt",
            "name": "Apt",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {"id": "i1", "label": "1st", "due_date": "2026-07-10", "amount": 1000}
            ],
        }
    )
    monkeypatch.setattr(
        "backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: 50000.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_income", lambda base, *_a, **_k: 0.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_expenses", lambda base, *_a, **_k: 0.0
    )

    cov = cs.coverage(months=6)
    assert cov["at_risk"] is False
    assert cov["total_shortfall_base"] == 0


def test_detect_unmarked_payments_matches_and_skips(isolated, monkeypatch):
    # A purchase installment with a same-amount txn in the date window is suggested;
    # goal commitments (trackers), already-linked installments, and out-of-window
    # txns are all skipped.
    CommitmentsStore().create(
        {
            "id": "apt",
            "name": "Apt",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {"id": "i1", "label": "1st", "due_date": "2026-06-01", "amount": 1000},
                {
                    "id": "i2",
                    "label": "2nd",
                    "due_date": "2026-07-01",
                    "amount": 1000,
                    "paid_txn_id": "t9",
                },
            ],
        }
    )
    CommitmentsStore().create(
        {
            "id": "dca",
            "name": "DCA",
            "kind": "goal",
            "currency": "SGD",
            "installments": [
                {"id": "g1", "label": "Month 1", "due_date": "2026-06-01", "amount": 1000}
            ],
        }
    )
    txns = [
        {"id": "t1", "date": "2026-06-03"},
        {"id": "t2", "date": "2026-09-01"},  # outside the ±7d window
    ]
    monkeypatch.setattr(cs.svc.transactions, "all", lambda: txns)
    monkeypatch.setattr(cs.svc, "txn_base_amount", lambda t, base, rates: 1000.0)

    suggestions = cs.detect_unmarked_payments("SGD", RATES)
    assert [(s["installment_id"], s["txn_id"]) for s in suggestions] == [("i1", "t1")]
    assert suggestions[0]["commitment_id"] == "apt"
    assert suggestions[0]["amount_base"] == 1000.0


def test_coverage_tracker_kinds_not_assessed(isolated, monkeypatch):
    # Regression: goal/auto_pay are trackers the engine never evaluates — their verdict
    # must carry assessed=False so the UI doesn't badge their installments "covered"
    # (at_risk=False alone is ambiguous between "covered" and "never looked").
    CommitmentsStore().create(
        {
            "id": "dca",
            "name": "VOO DCA",
            "kind": "goal",
            "currency": "SGD",
            "installments": [
                {"id": "i1", "label": "Month 1", "due_date": "2026-06-30", "amount": 2000}
            ],
        }
    )
    CommitmentsStore().create(
        {
            "id": "apt",
            "name": "Apt",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {"id": "i2", "label": "1st", "due_date": "2026-07-10", "amount": 1000}
            ],
        }
    )
    monkeypatch.setattr(
        "backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: 50000.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_income", lambda base, *_a, **_k: 0.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_expenses", lambda base, *_a, **_k: 0.0
    )

    cov = cs.coverage(months=6)
    dca = next(c for c in cov["commitments"] if c["id"] == "dca")
    apt = next(c for c in cov["commitments"] if c["id"] == "apt")
    assert dca["assessed"] is False and dca["at_risk"] is False
    assert apt["assessed"] is True


def test_coverage_is_floor_aware(isolated, monkeypatch):
    # The PORTFOLIO cushion (household-blended projection) is floor-aware: a commitment
    # that leaves it positive but under the cash floor must flag the household verdict
    # at-risk — "risk = dips below the floor", not "risk = goes negative" (2026-07-22
    # commitments redesign). The PER-COMMITMENT verdict is PLAN-FIDELITY (2026-07-31
    # redesign) and floors at $0 always — `floor` has no effect on it.
    CommitmentsStore().create(
        {
            "id": "apt",
            "name": "Apt",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {"id": "i1", "label": "1st", "due_date": "2026-07-10", "amount": 47000}
            ],
            "funding_plan": [
                {
                    "id": "f1",
                    "type": "cash",
                    "label": "Set-aside",
                    "amount": 50000,
                    "currency": "SGD",
                    "available_date": "2026-07-10",
                }
            ],
        }
    )
    monkeypatch.setattr(
        "backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: 50000.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_income", lambda base, *_a, **_k: 0.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_expenses", lambda base, *_a, **_k: 0.0
    )

    # Household cushion lands at 3000 — positive, but under a 5000 floor.
    cov_default = cs.coverage(months=6, floor=5000)
    assert cov_default["at_risk"] is True
    # With no floor of the user's own there is none: a positive cushion is not a shortfall
    # (a flat 5000 fallback alarmed a new user holding 2,150 — persona QA 2026-10-13).
    assert cs.coverage(months=6)["at_risk"] is False
    # This commitment's OWN plan-fidelity trace starts at $0 (household cash, incl. anything
    # unrelated like a margin balance, is deliberately excluded) and applies only its own
    # funding (50000) against its own installment (47000) = 3000 — never dips below $0, so not
    # at risk, regardless of the household's 5000 floor.
    apt = next(c for c in cov_default["commitments"] if c["id"] == "apt")
    assert apt["at_risk"] is False
    assert apt["shortfall_base"] == 0

    # floor=0 recovers the prior household negative-balance-only reading exactly; the
    # per-commitment verdict is unchanged (it was already a $0 floor).
    cov_zero = cs.coverage(months=6, floor=0)
    assert cov_zero["at_risk"] is False
    assert cov_zero["total_shortfall_base"] == 0
    apt_zero = next(c for c in cov_zero["commitments"] if c["id"] == "apt")
    assert apt_zero["at_risk"] is False


# ── Per-commitment plan-fidelity regression (2026-07-31) ────────────────────────
# Real-data bug: a commitment's near-term installment WAS fully funded by a same-window
# loan draw, yet the badge read "short $87,824 by Sep 30, 2026" — the dollar figure came
# from a distant 2028-09 Handover shortfall while the date came from the near-term (funded)
# installment, because the old code paired two independent reductions (`worst`/`earliest`)
# over the household projection instead of one self-consistent trace. See
# docs/adr/2026-07-31-commitment-coverage-plan-fidelity.md.


def test_coverage_plan_fidelity_pairing_is_self_consistent(isolated, monkeypatch):
    # Near-term installment is fully covered by a same-day loan draw (tie-break: funding
    # lands before the same-day outflow); a much later, unrelated Handover installment has
    # no funding at all and is the genuine gap. The reported date/dollar pair must describe
    # THAT event, never a mismatched pairing from an unrelated month.
    CommitmentsStore().create(
        {
            "id": "condo",
            "name": "Condo",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {"id": "i1", "label": "Booking", "due_date": "2026-07-10", "amount": 50000},
                {"id": "i2", "label": "Handover", "due_date": "2028-09-30", "amount": 200000},
            ],
            "funding_plan": [
                {
                    "id": "f1",
                    "type": "loan",
                    "label": "Bridge loan",
                    "amount": 50000,
                    "currency": "SGD",
                    "available_date": "2026-07-10",  # same day as the Booking installment
                }
            ],
        }
    )
    # Household cash is irrelevant to the per-commitment trace (it starts at $0, deliberately
    # ignoring the household position) — set to something nonzero to prove it has no effect.
    monkeypatch.setattr(
        "backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: 1000.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_income", lambda base, *_a, **_k: 0.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_expenses", lambda base, *_a, **_k: 0.0
    )

    cov = cs.coverage(months=40)
    condo = next(c for c in cov["commitments"] if c["id"] == "condo")

    assert condo["at_risk"] is True
    # The gap is the unfunded Handover installment, not the loan-covered Booking one.
    assert condo["earliest_shortfall_date"] == "2028-09-30"
    # 0 (plan-fidelity anchor) + 50000 loan - 50000 Booking - 200000 Handover = -200000.
    assert condo["shortfall_base"] == pytest.approx(200000.0)


def test_coverage_plan_fidelity_counts_income_set_aside_as_inflow(isolated, monkeypatch):
    # Owner complaint: an explicit "Set-aside" (funding_plan type=income) was silently
    # dropped by the old projected_events() filter (_NEW_MONEY_TYPES excludes income/cash),
    # so a fully-funded plan still badged as short. Plan-fidelity counts EVERY funding_plan
    # entry, income/cash included.
    CommitmentsStore().create(
        {
            "id": "school",
            "name": "School fees",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {"id": "i1", "label": "Term 1", "due_date": "2026-08-01", "amount": 2000}
            ],
            "funding_plan": [
                {
                    "id": "f1",
                    "type": "income",
                    "label": "Set-aside",
                    "amount": 2000,
                    "currency": "SGD",
                    "available_date": "2026-07-15",
                    "cadence": "monthly",
                }
            ],
        }
    )
    monkeypatch.setattr("backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: 0.0)
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_income", lambda base, *_a, **_k: 0.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_expenses", lambda base, *_a, **_k: 0.0
    )

    cov = cs.coverage(months=6)
    school = next(c for c in cov["commitments"] if c["id"] == "school")
    assert school["at_risk"] is False
    assert school["shortfall_base"] == 0


def test_coverage_plan_fidelity_ignores_unrelated_household_debt(isolated, monkeypatch):
    # Owner complaint: today's real household cash was deeply negative (e.g. a margin-account
    # debit unrelated to this purchase), so anchoring the per-commitment trace on it made a
    # fully-funded installment look short from the very first funding event. Plan-fidelity must
    # judge this commitment purely on its own funding vs. its own installments — a household
    # debt of any size must never leak into a single commitment's verdict.
    CommitmentsStore().create(
        {
            "id": "reno",
            "name": "Reno",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {"id": "i1", "label": "1st", "due_date": "2026-08-31", "amount": 10000}
            ],
            "funding_plan": [
                {
                    "id": "f1",
                    "type": "loan",
                    "label": "Reno loan",
                    "amount": 10000,
                    "currency": "SGD",
                    "available_date": "2026-08-15",
                }
            ],
        }
    )
    # A large, unrelated household debt (e.g. margin debit) — must have zero effect below.
    monkeypatch.setattr(
        "backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: -50000.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_income", lambda base, *_a, **_k: 0.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_expenses", lambda base, *_a, **_k: 0.0
    )

    cov = cs.coverage(months=6)
    reno = next(c for c in cov["commitments"] if c["id"] == "reno")
    assert reno["at_risk"] is False
    assert reno["shortfall_base"] == 0


def test_coverage_plan_fidelity_batches_same_day_outflows(isolated, monkeypatch):
    # Real-data bug found immediately after shipping the $0-anchor fix: two installments
    # (Handover + Maintenance) land on the SAME due_date. The trace applied them one at a
    # time and returned as soon as the FIRST one alone tipped the balance negative — silently
    # dropping the second same-day outflow from the reported shortfall. Both must be folded
    # into the balance before it's checked, so the reported gap reflects the whole day.
    CommitmentsStore().create(
        {
            "id": "condo2",
            "name": "Condo 2",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {"id": "i1", "label": "Handover", "due_date": "2028-09-30", "amount": 100000},
                {"id": "i2", "label": "Maintenance", "due_date": "2028-09-30", "amount": 10000},
            ],
            "funding_plan": [
                {
                    "id": "f1",
                    "type": "cash",
                    "label": "Set-aside",
                    "amount": 95000,
                    "currency": "SGD",
                    "available_date": "2028-09-01",
                }
            ],
        }
    )
    monkeypatch.setattr("backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: 0.0)
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_income", lambda base, *_a, **_k: 0.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_expenses", lambda base, *_a, **_k: 0.0
    )

    cov = cs.coverage(months=40)
    condo2 = next(c for c in cov["commitments"] if c["id"] == "condo2")
    assert condo2["at_risk"] is True
    assert condo2["earliest_shortfall_date"] == "2028-09-30"
    # 95000 - 100000 - 10000 = -15000. A bug that stops after just the 100000 outflow would
    # under-report this as 5000.
    assert condo2["shortfall_base"] == pytest.approx(15000.0)


def test_coverage_timeline_shape(isolated, monkeypatch):
    monkeypatch.setattr(
        "backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: 10000.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_income", lambda base, *_a, **_k: 0.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_expenses", lambda base, *_a, **_k: 0.0
    )

    cov = cs.coverage(months=6)
    assert len(cov["timeline"]) == 6
    first = cov["timeline"][0]
    assert set(first) == {"year_month", "balance", "total_in", "total_out", "is_tight"}


def test_coverage_timeline_is_tight_respects_floor_override(isolated, monkeypatch):
    # Regression (code review finding): `timeline[].is_tight` must be recomputed against the
    # CALLER's floor, not silently pulled from build_projection()'s profile-threshold-only
    # reading — otherwise a floor override could disagree with the rest of the same payload.
    monkeypatch.setattr(
        "backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: 8000.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_income", lambda base, *_a, **_k: 0.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_expenses", lambda base, *_a, **_k: 0.0
    )

    # balance stays flat at 8000 all along (no commitments) — tight under a 10000 floor,
    # not tight under a 5000 floor.
    tight = cs.coverage(months=3, floor=10_000)
    assert all(m["is_tight"] for m in tight["timeline"])
    loose = cs.coverage(months=3, floor=5_000)
    assert not any(m["is_tight"] for m in loose["timeline"])


def test_coverage_default_window_is_the_shared_horizon_and_names_its_low_point(
    isolated, monkeypatch
):
    # Regression (public-demo launch check): the Overview flag read coverage() at its old
    # 40-month default while the Commitments hero read it at 12, so the same April-2027 first
    # dip quoted $264,821 on one screen and $73,012 on the other — each the TROUGH of a
    # different window. The unparameterised call is now the one shared horizon, and the
    # payload names the window and the month the quoted gap is reached in.
    CommitmentsStore().create(
        {
            "id": "big",
            "name": "Big purchase",
            "kind": "purchase",
            "currency": "SGD",
            "installments": [
                {"id": "i1", "label": "Deposit", "due_date": "2026-08-10", "amount": 8000},
                {"id": "i2", "label": "Balance", "due_date": "2027-09-10", "amount": 90000},
            ],
        }
    )
    monkeypatch.setattr(
        "backend.services.cashflow.cash_balance_base", lambda base, *_a, **_k: 10000.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_income", lambda base, *_a, **_k: 0.0
    )
    monkeypatch.setattr(
        "backend.services.cashflow._rolling_avg_expenses", lambda base, *_a, **_k: 0.0
    )

    default = cs.coverage(floor=5_000)
    within = cs.coverage(months=cs.COVERAGE_HORIZON_MONTHS, floor=5_000)
    wider = cs.coverage(months=24, floor=5_000)

    assert default == within
    assert default["horizon_months"] == cs.COVERAGE_HORIZON_MONTHS == 12
    # First dip is the August deposit; the 12-month trough is that same month (the 90000
    # balance lands after the window), so the gap is shallow and the dates agree.
    assert default["earliest_shortfall_date"].startswith("2026-08")
    assert default["lowest_balance_month"] == "2026-08"
    assert default["total_shortfall_base"] == pytest.approx(3000.0)
    # A wider window reaches a deeper trough in a LATER month — the figure belongs to its window.
    assert wider["horizon_months"] == 24
    assert wider["lowest_balance_month"] == "2027-09"
    assert wider["total_shortfall_base"] == pytest.approx(93000.0)
    assert wider["earliest_shortfall_date"] == default["earliest_shortfall_date"]


# ── funding execution state: "missed" is derived, never stored (2026-08-23) ────


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({}, True),  # dated yesterday, still planned → overdue
        ({"available_date": "2026-08-23"}, False),  # due TODAY is not yet missed
        ({"available_date": "2026-08-24"}, False),  # future
        ({"status": "done"}, False),  # it happened
        ({"disabled": True}, False),  # "not happening" pulled it from the plan
        ({"available_date": None}, False),  # undated → cannot be placed on a trace
    ],
)
def test_funding_source_overdue_boundaries(overrides, expected):
    """A stored `missed` is only true if something sweeps the table daily; deriving it from
    `available_date` is correct the moment the clock passes the date."""
    source = {
        "status": "planned",
        "disabled": False,
        "available_date": "2026-08-22",
        **overrides,
    }
    assert cs.funding_source_overdue(source, today="2026-08-23") is expected


# ── paid_txn_id must name a real transaction (2026-10-13 audit) ─────────────────


def _with_installment():
    return CommitmentsStore().create(
        {
            "name": "Condo",
            "currency": "SGD",
            "installments": [
                {"id": "inst-1", "label": "1st", "due_date": "2026-07-01", "amount": 100}
            ],
        }
    )


def test_update_installment_refuses_a_paid_txn_id_that_is_not_in_the_ledger(isolated):
    # A bogus id used to mark the installment paid and drop it from projected cash flow.
    from backend.core.errors import ValidationError

    c = _with_installment()
    with pytest.raises(ValidationError, match="txn_gone"):
        cs.update_installment(c["id"], "inst-1", {"paid_txn_id": "txn_gone"})
    assert cs.get_commitment(c["id"])["installments"][0]["paid_txn_id"] is None


def test_set_and_add_installments_refuse_a_dead_paid_txn_id(isolated):
    from backend.core.errors import ValidationError

    c = _with_installment()
    row = {"label": "2nd", "due_date": "2026-08-01", "amount": 100, "paid_txn_id": "txn_gone"}
    with pytest.raises(ValidationError):
        cs.add_installments(c["id"], [row])
    with pytest.raises(ValidationError):
        cs.set_installments(c["id"], [row])
    assert len(cs.get_commitment(c["id"])["installments"]) == 1


def test_update_installment_links_a_real_transaction(isolated):
    from backend.db.account_store import AccountStore
    from backend.services import ledger

    acct = AccountStore().create({"name": "Checking", "type": "cash", "currency": "SGD"})
    txn = ledger.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "Condo 1st",
            "from_account_id": acct["id"],
            "from_amount": 100.0,
        }
    )
    c = _with_installment()
    cs.update_installment(c["id"], "inst-1", {"paid_txn_id": txn["id"]})
    assert cs.get_commitment(c["id"])["installments"][0]["paid_txn_id"] == txn["id"]


def test_spans_currencies_flags_a_foreign_account_commitment_or_funding_source(isolated):
    """The shortfall moves with FX only when something in the verdict is converted."""
    assert cs._spans_currencies("SGD", []) is False
    assert cs._spans_currencies("SGD", [{"currency": "SGD", "funding_plan": []}]) is False
    assert cs._spans_currencies("SGD", [{"currency": "USD", "funding_plan": []}]) is True
    plan = [{"currency": "SGD"}, {"currency": "VND"}]
    assert cs._spans_currencies("SGD", [{"currency": "SGD", "funding_plan": plan}]) is True

    cs.svc.accounts.create({"name": "Wise USD", "kind": "asset", "type": "cash", "currency": "USD"})
    assert cs._spans_currencies("SGD", []) is True
