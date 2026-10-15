"""
Unit tests for services.reports — the report library (P4-RP1).

A report is a COMPOSITION of existing read services, so these tests monkeypatch those services
to canned payloads and assert (a) the composed report MODEL shape and (b) that `render_html`
produces a self-contained printable document with the expected figures escaped/formatted. An
empty/young book is driven through end-to-end (no monkeypatch) to prove it degrades to empty
tables rather than raising.

Run:
    .venv/bin/python3 -m pytest tests/test_reports_service.py -q
"""

from __future__ import annotations

import pytest

from backend.services import categories as categories_service
from backend.services import reports
from backend.services.budget_engines.types import Group, Headline, Health, Measure, Row, ViewModel

# ── catalog ─────────────────────────────────────────────────────────────────────


def test_list_reports_catalog_shape():
    cat = reports.list_reports()
    types = {r["type"] for r in cat}
    assert types == {
        "net_worth_statement",
        "realized_income",
        "spending_summary",
        "cash_flow_statement",
        "budget_vs_actual",
    }
    for r in cat:
        assert r["title"] and r["description"]
        assert isinstance(r["uses_period"], bool)


def test_build_report_unknown_type_raises():
    with pytest.raises(ValueError):
        reports.build_report("does_not_exist")


# ── net-worth statement (composed) ─────────────────────────────────────────────────


def _patch_net_worth(monkeypatch):
    monkeypatch.setattr(reports, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        reports.ledger_service,
        "net_worth",
        lambda base=None: {
            "base_currency": "USD",
            "net_worth": 12345.67,
            "breakdown": {
                "accounts_asset": 8000.0,
                "accounts_liability": -1000.0,
                "market_positions": 5345.67,
            },
        },
    )
    monkeypatch.setattr(reports.ledger_service.transactions, "all", lambda: [])

    class _Accts:
        def query_accounts(self, *a, **k):
            return [
                {
                    "id": "a1",
                    "name": "Chequing",
                    "kind": "asset",
                    "type": "cash",
                    "currency": "USD",
                },
                {
                    "id": "l1",
                    "name": "Visa",
                    "kind": "liability",
                    "type": "credit",
                    "currency": "USD",
                },
            ]

    monkeypatch.setattr(reports, "AccountStore", _Accts)
    monkeypatch.setattr(
        reports.ledger_service,
        "account_with_balance",
        lambda a, txns=None, *, idx=None: {**a, "balance": 100.0, "balance_base": 100.0},
    )
    monkeypatch.setattr(
        reports.returns_service,
        "portfolio_returns",
        lambda period: {
            "period": period,
            "period_gain_base": 500.0,
            "twr": {"period_return": 0.10, "annualized": 0.10},
            "mwr": {"xirr": 0.12},
            "data_quality": {"sufficient": True},
        },
    )


def test_net_worth_statement_model(monkeypatch):
    _patch_net_worth(monkeypatch)
    report = reports.build_report("net_worth_statement", "1Y")
    assert report["type"] == "net_worth_statement"
    assert report["base_currency"] == "USD"
    assert report["period"] == "1Y"
    data = report["data"]
    assert data["net_worth"] == 12345.67
    # one asset + one liability split by kind
    assert [r["name"] for r in data["assets"]] == ["Chequing"]
    assert [r["name"] for r in data["liabilities"]] == ["Visa"]


def test_net_worth_statement_html(monkeypatch):
    _patch_net_worth(monkeypatch)
    report = reports.build_report("net_worth_statement", "1Y")
    html = reports.render_html(report)
    assert html.startswith("<!doctype html>")
    assert "Net-worth statement" in html
    assert "USD 12,345.67" in html  # formatted net worth
    assert "10.00%" in html  # TWR rendered as percent (fraction → pct)
    assert "12.00%" in html  # XIRR
    assert "@media print" in html  # print stylesheet present


def test_net_worth_statement_insufficient_returns_render(monkeypatch):
    _patch_net_worth(monkeypatch)
    monkeypatch.setattr(
        reports.returns_service,
        "portfolio_returns",
        lambda period: {"period": period, "data_quality": {"sufficient": False}},
    )
    html = reports.render_html(reports.build_report("net_worth_statement", "1M"))
    assert "Not enough price history" in html


# ── realized + income (composed) ───────────────────────────────────────────────────


def _patch_realized_income(monkeypatch):
    monkeypatch.setattr(reports, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        reports.returns_service, "resolve_period", lambda p: ("2025-01-01", "2025-12-31")
    )
    monkeypatch.setattr(
        reports.lots_service,
        "realized_pnl",
        lambda **k: {
            "base_currency": "USD",
            "realized_total_base": 750.0,
            "by_term": {"long": 600.0, "short": 150.0, "unknown": 0.0},
            "events": [
                {
                    "disposed_at": "2025-03-01",
                    "instrument_name": "Acme Inc",
                    "quantity": 10,
                    "realized_gain_base": 750.0,
                }
            ],
        },
    )
    monkeypatch.setattr(
        reports.income_service,
        "income_summary",
        lambda **k: {
            "base_currency": "USD",
            "total_base": 200.0,
            "gross_base": 240.0,
            "tax_withheld_base": 40.0,
            "trailing_12m_base": 200.0,
            "yield_on_cost_pct": 3.5,
            "by_kind": {"dividend": 200.0},
        },
    )


def test_realized_income_model_and_html(monkeypatch):
    _patch_realized_income(monkeypatch)
    report = reports.build_report("realized_income", "YTD")
    assert report["data"]["realized"]["realized_total_base"] == 750.0
    html = reports.render_html(report)
    assert "Realized gains" in html
    assert "USD 750.00" in html
    assert "Acme Inc" in html
    assert "3.50%" in html  # yield on cost


def test_realized_income_unknown_gain_renders_dash(monkeypatch):
    _patch_realized_income(monkeypatch)
    monkeypatch.setattr(
        reports.lots_service,
        "realized_pnl",
        lambda **k: {
            "base_currency": "USD",
            "realized_total_base": 0.0,
            "by_term": {"long": 0.0, "short": 0.0, "unknown": 0.0},
            "events": [
                {
                    "disposed_at": "2025-03-01",
                    "instrument_name": "Mystery",
                    "quantity": 1,
                    "realized_gain_base": None,
                }
            ],
        },
    )
    html = reports.render_html(reports.build_report("realized_income", "YTD"))
    assert "Mystery" in html
    assert "—" in html  # unknown gain is a dash, not a fabricated 0


# ── spending summary (composed) ─────────────────────────────────────────────────────


def test_spending_summary_model_and_html(monkeypatch):
    monkeypatch.setattr(reports, "base_currency", lambda: "USD")
    # Sourced from the book's HOME budget lens now (RFC 2026-08-22-pluggable-budget-engines.md
    # §8.1) — budget_lens.home_spending_summary, not services.budgets.spending_summary.
    monkeypatch.setattr(
        reports.budget_lens_service,
        "home_spending_summary",
        lambda month=None, year=None, base=None: {
            "month": 6,
            "year": 2026,
            "total_spent": 1500.0,
            "total_available": 2000.0,
            "pct_budget_used": 75.0,
            "daily_rate": 50.0,
            "projected_total": 1500.0,
            "by_category": [
                {
                    "category": "food",
                    "is_expense": True,
                    "spent": 400.0,
                    "available": 500.0,
                    "pct_used": 80.0,
                    "status": "on_track",
                }
            ],
            "by_source": [{"name": "Visa", "spent": 1500.0, "count": 12}],
        },
    )
    report = reports.build_report("spending_summary")
    assert report["period"] is None  # not period-driven
    html = reports.render_html(report)
    assert "Spending summary" in html
    assert "June 2026" in html
    assert "USD 1,500.00" in html
    assert "food" in html
    assert "on track" in html  # status underscore replaced


# ── empty book degrades (end-to-end, no monkeypatch) ────────────────────────────────


def test_empty_book_builds_and_renders_each_report():
    for rt in (
        "net_worth_statement",
        "realized_income",
        "spending_summary",
        "cash_flow_statement",
        "budget_vs_actual",
    ):
        report = reports.build_report(rt, "1Y")
        html = reports.render_html(report)
        assert html.startswith("<!doctype html>")
        assert report["base_currency"]  # base is resolved, never blank


# ── cash-flow statement (composed) ───────────────────────────────────────────────────


def _patch_cash_flow(monkeypatch):
    monkeypatch.setattr(reports, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        reports.returns_service, "resolve_period", lambda p: ("2025-01-01", "2025-12-31")
    )
    monkeypatch.setattr(reports, "load_fx_cache", lambda: {})
    monkeypatch.setattr(reports.ledger_service, "txn_base_amount", lambda t, base, rates: t["amt"])
    txns = [
        {"kind": "income", "category": "salary", "date": "2025-03-01", "amt": 5000.0},
        {"kind": "income", "category": "salary", "date": "2025-04-01", "amt": 5000.0},
        {"kind": "expense", "category": "food", "date": "2025-03-02", "amt": 1200.0},
        {"kind": "expense", "category": "transport", "date": "2025-03-03", "amt": 300.0},
        # out-of-window + deleted + transfer are excluded
        {"kind": "expense", "category": "food", "date": "2024-12-31", "amt": 999.0},
        {"kind": "expense", "category": "food", "date": "2025-05-01", "amt": 1.0, "deleted": True},
        {"kind": "transfer", "date": "2025-03-04", "amt": 9999.0},
    ]
    monkeypatch.setattr(reports.ledger_service.transactions, "all", lambda: txns)


def test_cash_flow_statement_model(monkeypatch):
    _patch_cash_flow(monkeypatch)
    report = reports.build_report("cash_flow_statement", "1Y")
    d = report["data"]
    assert d["income_total"] == 10000.0
    assert d["expense_total"] == 1500.0
    assert d["net"] == 8500.0
    assert d["savings_rate_pct"] == 85.0
    # grouped by category, transfers/deleted/out-of-window excluded
    assert {r["category"] for r in d["income_by_category"]} == {"salary"}
    assert {r["category"] for r in d["expense_by_category"]} == {"food", "transport"}


def test_cash_flow_statement_html(monkeypatch):
    _patch_cash_flow(monkeypatch)
    html = reports.render_html(reports.build_report("cash_flow_statement", "1Y"))
    assert "Cash-flow statement" in html
    assert "USD 8,500.00" in html  # net
    assert "85.00%" in html  # savings rate (_pct renders 2 decimals)
    assert "Food" in html  # "food" isn't a real category id, so the label falls back title-cased


def test_cash_flow_no_income_savings_rate_dash(monkeypatch):
    _patch_cash_flow(monkeypatch)
    monkeypatch.setattr(
        reports.ledger_service.transactions,
        "all",
        lambda: [{"kind": "expense", "category": "food", "date": "2025-03-02", "amt": 100.0}],
    )
    report = reports.build_report("cash_flow_statement", "1Y")
    assert report["data"]["savings_rate_pct"] is None  # never a fabricated rate without income
    html = reports.render_html(report)
    assert "—" in html


def test_cash_flow_statement_labels_categories(monkeypatch):
    """MATH-15 regression: grouping is by opaque category id, but both the model and the
    render must show the human label (``services.categories``), never the raw ``cat_xxx`` id —
    the id stays available in its own field for a caller that needs it."""
    monkeypatch.setattr(reports, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        reports.returns_service, "resolve_period", lambda p: ("2025-01-01", "2025-12-31")
    )
    monkeypatch.setattr(reports, "load_fx_cache", lambda: {})
    monkeypatch.setattr(reports.ledger_service, "txn_base_amount", lambda t, base, rates: t["amt"])
    groceries = categories_service.create_category(label="Groceries", kind="expense")["id"]
    txns = [{"kind": "expense", "category": groceries, "date": "2025-03-02", "amt": 55.0}]
    monkeypatch.setattr(reports.ledger_service.transactions, "all", lambda: txns)

    report = reports.build_report("cash_flow_statement", "1Y")
    rows = report["data"]["expense_by_category"]
    assert len(rows) == 1
    assert rows[0]["category"] == groceries  # opaque id still available
    assert rows[0]["category_label"] == "Groceries"  # resolved for display

    html = reports.render_html(report)
    assert "Groceries" in html
    assert groceries not in html  # never the raw id


def test_cash_flow_statement_expands_splits_by_category(monkeypatch):
    """MATH-16 regression: a split expense's per-line categories drive the breakdown — never
    one lumped "split" row — while the total stays exact."""
    monkeypatch.setattr(reports, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        reports.returns_service, "resolve_period", lambda p: ("2025-01-01", "2025-12-31")
    )
    monkeypatch.setattr(reports, "load_fx_cache", lambda: {})
    groceries = categories_service.create_category(label="Groceries", kind="expense")["id"]
    dining = categories_service.create_category(label="Dining", kind="expense")["id"]
    txns = [
        {
            "kind": "expense",
            "category": "split",
            "date": "2025-03-02",
            "from_amount": 100.0,
            "from_currency": "USD",
            "base_amount": 100.0,
            "base_currency": "USD",
            "splits": [
                {"category": groceries, "amount": 70.0},
                {"category": dining, "amount": 30.0},
            ],
        }
    ]
    monkeypatch.setattr(reports.ledger_service.transactions, "all", lambda: txns)

    report = reports.build_report("cash_flow_statement", "1Y")
    d = report["data"]
    assert d["expense_total"] == 100.0  # totals unaffected — only the breakdown changes
    rows = {r["category"]: r["amount"] for r in d["expense_by_category"]}
    assert rows == {groceries: 70.0, dining: 30.0}
    assert "split" not in rows

    html = reports.render_html(report)
    assert "Groceries" in html
    assert "Dining" in html


def test_cash_flow_statement_unsorted_category_labelled(monkeypatch):
    """A blank-category leg still gets its own labelled 'Uncategorized' row."""
    monkeypatch.setattr(reports, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        reports.returns_service, "resolve_period", lambda p: ("2025-01-01", "2025-12-31")
    )
    monkeypatch.setattr(reports, "load_fx_cache", lambda: {})
    monkeypatch.setattr(reports.ledger_service, "txn_base_amount", lambda t, base, rates: t["amt"])
    txns = [{"kind": "expense", "category": None, "date": "2025-03-02", "amt": 40.0}]
    monkeypatch.setattr(reports.ledger_service.transactions, "all", lambda: txns)

    report = reports.build_report("cash_flow_statement", "1Y")
    rows = report["data"]["expense_by_category"]
    assert len(rows) == 1
    assert rows[0]["category_label"] == "Uncategorized"

    html = reports.render_html(report)
    assert "Uncategorized" in html


# ── budget vs actual (composed) ──────────────────────────────────────────────────────


def _patch_budget(monkeypatch):
    monkeypatch.setattr(reports, "base_currency", lambda: "USD")
    # These two tests fake `budget_month`, so they are exercising the ZERO-BASED rendering
    # path specifically. Pin the home engine to match: an un-budgeted book now resolves to
    # "proportional" (services.budget_engines.base._unset_home_engine_id, owner 2026-08-23),
    # which sends _budget_vs_actual down the generic branch and leaves the faked data unread.
    monkeypatch.setattr(reports.budget_engines_base, "home_engine_id", lambda: "zero_based")
    monkeypatch.setattr(
        reports.returns_service, "resolve_period", lambda p: ("2025-04-01", "2025-05-31")
    )

    def fake_budget_month(month):
        # two months in window — assigned/activity accumulate; available from latest only.
        per_month = {
            "2025-04": {"food": (200.0, -150.0), "rent": (1000.0, -1000.0)},
            "2025-05": {"food": (200.0, -180.0), "rent": (1000.0, -1000.0)},
        }
        avail = {"2025-05": {"food": 70.0, "rent": 0.0}}.get(month, {})
        cats = per_month.get(month, {})
        return {
            "month": month,
            "base_currency": "USD",
            "groups": [
                {
                    "name": "Essentials",
                    "categories": [
                        {
                            "key": k,
                            "label": k.title(),
                            "assigned": cats[k][0],
                            "activity": cats[k][1],
                            "available": avail.get(k, 0.0),
                        }
                        for k in cats
                    ],
                }
            ],
        }

    monkeypatch.setattr(reports.planning_service, "budget_month", fake_budget_month)


def test_budget_vs_actual_model(monkeypatch):
    _patch_budget(monkeypatch)
    report = reports.build_report("budget_vs_actual", "1M")
    d = report["data"]
    assert d["months"] == ["2025-04", "2025-05"]
    g = d["groups"][0]
    food = next(c for c in g["categories"] if c["key"] == "food")
    # assigned + activity SUMMED across the two months; available from the latest month only
    assert food["assigned"] == 400.0
    assert food["activity"] == -330.0
    assert food["available"] == 70.0
    assert d["totals"]["assigned"] == 2400.0
    assert d["totals"]["activity"] == -2330.0
    assert d["totals"]["available"] == 70.0


def test_budget_vs_actual_html(monkeypatch):
    _patch_budget(monkeypatch)
    html = reports.render_html(reports.build_report("budget_vs_actual", "1M"))
    assert "Budget vs actual" in html
    assert "Essentials" in html
    assert "Food" in html
    assert "subtotal" in html.lower()


def test_budget_vs_actual_empty_groups_render(monkeypatch):
    _patch_budget(monkeypatch)
    monkeypatch.setattr(
        reports.planning_service,
        "budget_month",
        lambda month: {"month": month, "base_currency": "USD", "groups": []},
    )
    html = reports.render_html(reports.build_report("budget_vs_actual", "1M"))
    assert "No Planning categories budgeted" in html


# ── budget vs actual — a non-zero-based home engine (BUG regression) ────────────────────
#
# `_render_budget_vs_actual` used to hardcode zero-based's assigned/activity/available keys
# with no branch on `data["engine_id"]`. Those keys never exist on `_budget_vs_actual_generic`'s
# engine-shaped rows, so every total and category row rendered as "—" once the home lens was
# 50/30/20 — this pins the fix: real values render, sourced from the engine's own
# `measure_labels`, not a hardcoded zero-based column set.


def _generic_view(month: str, cap: float, spent: float):
    row = Row(
        id="cat_groceries",
        label="Groceries",
        color=None,
        measures=[
            Measure(key="cap", label="Cap", amount_base=cap),
            Measure(key="spent", label="Spent", amount_base=spent),
        ],
        status="comfortable",
    )
    group = Group(id="needs", label="Needs", rows=[row], subtotal=[])
    return ViewModel(
        engine_id="proportional",
        period=month,
        base_currency="USD",
        headline=Headline(label="Saved", value=20.0, unit="percent", status="clear"),
        groups=[group],
        rows_shape="gauges",
        health=Health(verdict="good", reasons=["On target."]),
        unmapped=None,
    )


def _patch_generic_budget(monkeypatch):
    """Home lens = 50/30/20, two months in the window: every measure is a flow, summed
    across the months under its own name."""
    monkeypatch.setattr(reports, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        reports.returns_service, "resolve_period", lambda p: ("2025-04-01", "2025-05-31")
    )
    monkeypatch.setattr(reports.budget_engines_base, "home_engine_id", lambda: "proportional")
    views = {
        "2025-04": _generic_view("2025-04", cap=100.0, spent=80.0),
        "2025-05": _generic_view("2025-05", cap=100.0, spent=90.0),
    }
    monkeypatch.setattr(reports.budget_engines_service, "get_view", lambda engine_id, m: views[m])


def test_budget_vs_actual_generic_engine_model(monkeypatch):
    _patch_generic_budget(monkeypatch)
    report = reports.build_report("budget_vs_actual", "1M")
    d = report["data"]
    assert d["engine_id"] == "proportional"
    assert d["months"] == ["2025-04", "2025-05"]
    row = d["groups"][0]["categories"][0]
    assert row["cap"] == 200.0  # summed across both months
    assert row["spent"] == 170.0  # summed across both months (80 + 90)
    assert d["totals"] == {"cap": 200.0, "spent": 170.0}


def test_budget_vs_actual_generic_engine_html_renders_real_values_not_dashes(monkeypatch):
    _patch_generic_budget(monkeypatch)
    report = reports.build_report("budget_vs_actual", "1M")
    body = reports._render_budget_vs_actual(report["data"], report["base_currency"])
    assert "Groceries" in body
    assert "USD 200.00" in body  # Total Cap
    assert "USD 170.00" in body  # Total Spent
    assert "—" not in body  # the bug: every figure used to collapse to this


def test_bad_period_coerced_to_default():
    report = reports.build_report("net_worth_statement", "BOGUS")
    assert report["period"] == "1Y"


# ── internal-field leak regression ───────────────────────────────────────────────────


def test_net_worth_statement_does_not_leak_internal_fields(monkeypatch):
    """_value_pts and _flows must not appear in the report payload (they are internal
    fields consumed by portfolio_returns_with_benchmark; embedding them in the report
    JSON would leak the user's dated cash-flow amounts and a large daily array)."""
    _patch_net_worth(monkeypatch)
    # Override portfolio_returns to return a dict WITH the internal fields, simulating
    # the real portfolio_returns() output shape so the strip is actually exercised.
    monkeypatch.setattr(
        reports.returns_service,
        "portfolio_returns",
        lambda period: {
            "period": period,
            "period_gain_base": 100.0,
            "twr": {"period_return": 0.05, "annualized": 0.05},
            "mwr": None,
            "data_quality": {"sufficient": True},
            "_value_pts": [{"date": "2025-01-01", "value": 1000.0}],
            "_flows": [("2025-06-01", -500.0)],
        },
    )
    report = reports.build_report("net_worth_statement", "1Y")
    returns_in_report = report["data"]["returns"]
    assert "_value_pts" not in returns_in_report, "_value_pts must be stripped before embedding"
    assert "_flows" not in returns_in_report, "_flows must be stripped before embedding"
