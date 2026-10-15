"""The daily briefing reads the SAME authorities the Dashboard and Budget screens read.

Persona QA 2026-10-14 (a real book, reproduced here with synthetic data only): the note said
"nothing falls due in the next fortnight" beside a calendar with a loan debit tomorrow and six
overdue items, valued a holding at roughly twice what Investments showed, and quoted a monthly
budget and an "over budget" category the Budget screen never displayed. The briefing input now
carries the figures those screens compute, so the narration cannot contradict them.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import backend.api.briefing as brf
from backend.core.user_time import user_today
from backend.db.commitments_store import CommitmentsStore
from backend.services import briefing_context as facts
from backend.services import commitments as commitments_service


@pytest.fixture
def book(db_isolated, monkeypatch, fx_rates):
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})
    return db_isolated


def _day(offset: int) -> str:
    return (user_today() + timedelta(days=offset)).isoformat()


def _installment(i: str, label: str, offset: int, amount: float = 1_000.0) -> dict:
    return {"id": i, "label": label, "due_date": _day(offset), "amount": amount}


def _seed_loan(*installments: dict) -> None:
    CommitmentsStore().create(
        {
            "id": "loan1",
            "name": "Renovation Loan",
            "kind": "loan_repayment",
            "currency": "SGD",
            "installments": list(installments),
        }
    )


# ── due and overdue ───────────────────────────────────────────────────────────


def test_a_debit_tomorrow_and_the_overdue_items_reach_the_briefing(book):
    """Six overdue items and a debit tomorrow: the section may never read as empty."""
    _seed_loan(
        _installment("t", "tomorrow debit", 1, 1_234.0),
        *[_installment(f"o{n}", f"overdue {n}", -n) for n in range(1, 7)],
    )
    ctx = brf._build_context(0.0)

    assert "OVERDUE (6" in ctx
    assert "tomorrow debit" in ctx
    assert "S$1,234" in ctx
    assert "nothing overdue" not in ctx


def test_every_event_the_dashboard_feed_lists_is_in_the_briefing(book):
    """The briefing carries the feed the Next-30-days card reads, item for item."""
    _seed_loan(
        _installment("a", "first", 2),
        _installment("b", "second", 9),
        _installment("c", "late one", -3),
        _installment("far", "beyond the fortnight", 40),
    )
    feed = commitments_service.upcoming_events(days=14)
    ctx = brf._build_context(0.0)

    assert [e["label"] for e in feed] == ["late one", "first", "second"]
    for event in feed:
        assert event["label"] in ctx
    assert "beyond the fortnight" not in ctx


def test_an_empty_feed_says_so_plainly(book):
    ctx = brf._build_context(0.0)
    assert "None due and nothing overdue" in ctx


def test_overdue_lines_say_how_late_and_are_capped(book):
    _seed_loan(*[_installment(f"o{n}", f"late {n}", -n) for n in range(1, 10)])
    lines = facts.upcoming_lines(user_today(), "SGD", {"SGD": 1.0})

    assert lines[0] == "  OVERDUE (9 unpaid or not done, S$9,000 in all, oldest first):"
    assert "9d overdue" in lines[1]
    assert lines[1 + facts._OVERDUE_LISTED] == "  (+3 more overdue)"


# ── holdings ──────────────────────────────────────────────────────────────────


def _hold(symbol, name, quantity, *, price=100.0, ccy="SGD", vesting=None, account=None):
    from backend.db.instrument_store import InstrumentStore
    from backend.db.position_store import PositionStore

    existing = next((i for i in InstrumentStore().all() if i["symbol"] == symbol), None)
    inst = existing or InstrumentStore().create(
        {
            "symbol": symbol,
            "name": name,
            "asset_class": "equity",
            "quote_currency": ccy,
            "price_source": "manual",
            "price_source_id": symbol,
            "manual_price": price,
        }
    )
    PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": account,
            "quantity": quantity,
            "cost_basis": price,
            "cost_currency": ccy,
            "bucket": "liquid",
            **({"vesting_state": vesting} if vesting else {}),
        }
    )


def _account(name):
    from backend.services import accounts as accounts_service

    return accounts_service.create_account(
        {
            "name": name,
            "type": "investment",
            "currency": "SGD",
            "initial_balance": 0,
            "initial_balance_date": "2026-01-01",
        }
    )["id"]


@pytest.fixture
def no_live_prices(monkeypatch):
    from backend.services import prices as prices_service

    monkeypatch.setattr(prices_service, "fetch_live", lambda *a, **k: {"prices": {}, "fx": {}})


def test_a_holding_is_valued_as_investments_values_it(book, no_live_prices):
    """The same name at two accounts is ONE line at the book's summed value, and unvested
    grant units (not owned) are not in it — the source of the S$120K-vs-S$68K contradiction."""
    from backend.services.investments_book import investments_book

    a, b = _account("Broker A"), _account("Broker B")
    _hold("AAA", "Alpha Corp", 100, account=a)
    _hold("AAA", "Alpha Corp", 50, account=b)
    _hold("AAA", "Alpha Corp", 500, vesting="unvested")

    screen = next(r for r in investments_book()["merged_rows"] if r["symbol"] == "AAA")
    ctx = brf._build_context(0.0)

    assert screen["value"] == pytest.approx(15_000.0)  # (100 + 50) x 100 — the unvested 500 is out
    assert f"Alpha Corp (AAA) — {facts.money(screen['value'], 'SGD')}" in ctx
    assert ctx.count("(AAA)") == 1
    assert "S$65,000" not in ctx


def test_the_briefing_uses_no_live_price_round_trip(book, monkeypatch):
    from backend.services import prices as prices_service

    def _boom(*a, **k):
        raise AssertionError("a briefing load must not hit a price provider")

    monkeypatch.setattr(prices_service, "fetch_live", _boom)
    brf._build_context(0.0)


# ── the month's budget ────────────────────────────────────────────────────────


def _seed_zero_based(monkeypatch):
    table = {
        "ready_to_assign": 250.0,
        "totals": {"assigned": 2_000.0, "activity": -1_400.0, "available": 600.0},
        "groups": [
            {
                "categories": [
                    {"label": "Groceries", "available": -80.0},
                    {"label": "Dining", "available": 120.0},
                ]
            }
        ],
    }
    monkeypatch.setattr(facts.planning_service, "budget_month", lambda ym: table)
    monkeypatch.setattr(facts.budget_engines, "home_engine_id", lambda: "zero_based")


def test_zero_based_budget_figures_are_the_plan_screens(book, monkeypatch):
    _seed_zero_based(monkeypatch)
    lines = facts.budget_lines("2026-10", "SGD")

    assert "assigned S$2,000, spent S$1,400, still to assign S$250" in lines[0]
    assert "Groceries -S$80" in lines[1]
    assert "Dining" not in lines[1]


def test_an_unbudgeted_book_has_no_budget_lines(book, monkeypatch):
    monkeypatch.setattr(facts.planning_service, "budget_month", lambda ym: {"totals": {}})
    monkeypatch.setattr(facts.budget_engines, "home_engine_id", lambda: "zero_based")
    assert facts.budget_lines("2026-10", "SGD") == []


def _proportional_month() -> dict:
    return {
        "configured": True,
        "income": {"amount": 5_000.0},
        "unassigned": {"uncategorized": 100.0, "no_envelope": 50.0},
        "groups": [
            {"name": "Needs", "activity": 1_800.0, "target": 50, "excluded": False},
            {"name": "Wants", "activity": 900.0, "target": 30, "excluded": False},
            {"name": "Transfers", "activity": 4_000.0, "target": None, "excluded": True},
        ],
        "envelopes": [
            {"label": "Travel", "cap": 300.0, "activity": 420.0, "excluded": False},
            {"label": "Dining", "cap": 500.0, "activity": 450.0, "excluded": False},
            {"label": "Coffee", "cap": None, "activity": 99.0, "excluded": False},
        ],
    }


def test_50_30_20_figures_are_the_budget_screens_and_only_a_cap_makes_something_over(
    book, monkeypatch
):
    """The 50/30/20 screen reads 'spent of income' and has no per-category budget, so the
    briefing may say 'over' only for an envelope past its CAP."""
    monkeypatch.setattr(facts.budget_engines, "home_engine_id", lambda: "proportional")
    monkeypatch.setattr(
        facts.budget_engines_service, "proportional_month", lambda ym: _proportional_month()
    )
    lines = facts.budget_lines("2026-10", "SGD")

    assert "spent S$2,850 of S$5,000 income" in lines[0]  # 1800 + 900 + 100 + 50
    assert "ONLY if it is listed as over its cap" in lines[0]
    assert any("Needs: S$1,800, target 50% of income" in ln for ln in lines)
    assert not any("Transfers" in ln for ln in lines)
    assert lines[-1] == "  Over their cap: Travel by S$120"


def test_an_unconfigured_50_30_20_book_has_no_budget_lines(book, monkeypatch):
    monkeypatch.setattr(facts.budget_engines, "home_engine_id", lambda: "proportional")
    monkeypatch.setattr(
        facts.budget_engines_service, "proportional_month", lambda ym: {"configured": False}
    )
    assert facts.budget_lines("2026-10", "SGD") == []


def test_the_spending_pulse_carries_the_budget_screens_figures(book, monkeypatch):
    """No second budget definition: the pulse prints the home method's own month view, and
    never the ledger-sum-over-assigned 'monthly budget' it used to quote."""
    from backend.services import ledger as ledger_service
    from tests.conftest import seed_stub_accounts

    txn = {
        "id": "t1",
        "date": user_today().isoformat(),
        "kind": "expense",
        "category": "groceries",
        "description": "Supermarket",
        "from_account_id": "acc1",
        "amount": 200.0,
        "currency": "SGD",
        "base_amount": 200.0,
        "deleted": False,
    }
    seed_stub_accounts([txn])
    ledger_service.transactions.replace_all([txn])
    _seed_zero_based(monkeypatch)

    lines = brf._spending_block({}, "SGD")

    assert any("assigned S$2,000, spent S$1,400" in ln for ln in lines)
    assert not any("Month-to-date" in ln for ln in lines)
    assert not any("/mo" in ln for ln in lines)


# ── tone: time of day and one currency notation ───────────────────────────────


def test_the_context_names_the_local_time_and_the_prompt_forbids_a_wrong_greeting(book):
    ctx = brf._build_context(0.0)
    assert "local time (" in ctx
    prompt = " ".join(brf._SYSTEM.split())
    assert "ONLY when it matches the local time" in prompt
    assert "at night open with the point" in prompt


def test_money_is_written_one_way(book):
    assert facts.money(12_508, "SGD") == "S$12,508"
    assert facts.money(-300, "USD") == "-US$300"
    assert facts.money(1_200, "CHF") == "CHF 1,200"
    prompt = " ".join(brf._SYSTEM.split())
    assert 'Never switch notation, such as "SGD 12,508"' in prompt
