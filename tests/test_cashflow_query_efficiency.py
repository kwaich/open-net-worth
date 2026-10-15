"""Regression test (2026-07-27 perf investigation): `_historical_months` and
`_actual_month_flow` used to reload the entire category/transaction stores from disk inside
their per-month loops instead of loading once and reusing — a live profile of a 12-month
window measured 798 `CategoryStore().get()` calls (one per transaction) and dozens of
redundant `query_txns()` calls, which is what made `/api/cashflow` take 2-5s ALONE with zero
concurrent load, and dragged down unrelated endpoints (`/api/accounts`, etc.) sharing the
same GIL-bound process under real traffic.

Pins the call counts bounded (by a small constant, never scaling with transaction/month
count) so this can't silently regress.
"""

from __future__ import annotations

from datetime import date

from backend.db.category_store import CategoryStore
from backend.db.transaction_store import TransactionStore
from backend.services import cashflow
from backend.services import forecast_scorecard as fs


def _txn(i: int, category: str, kind: str = "expense") -> dict:
    return {
        "id": f"t{i}",
        "date": f"2026-06-{(i % 27) + 1:02d}",
        "kind": kind,
        "description": f"txn {i}",
        "category": category,
        "from_account_id": "bank" if kind == "expense" else None,
        "from_amount": 10.0 if kind == "expense" else None,
        "to_account_id": "bank" if kind == "income" else None,
        "to_amount": 10.0 if kind == "income" else None,
        "deleted": False,
    }


def _pin_today(monkeypatch, module, y=2026, m=7, d=7):
    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(y, m, d)

    monkeypatch.setattr(module, "user_today", _FixedDate.today)


def _count_calls(monkeypatch, cls, method_name):
    calls: list = []
    original = getattr(cls, method_name)

    def counting(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(cls, method_name, counting)
    return calls


def test_historical_months_does_not_reload_category_store_per_transaction(monkeypatch):
    txns = [_txn(i, category="cat_food" if i % 2 else "cat_rent") for i in range(200)]
    acct = {
        "id": "bank",
        "kind": "asset",
        "type": "cash",
        "currency": "USD",
        "initial_balance": 0.0,
    }

    monkeypatch.setattr(
        cashflow.svc.accounts, "query_accounts", lambda kind=None: [acct] if kind == "asset" else []
    )
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: txns)
    monkeypatch.setattr(
        cashflow.svc, "txn_base_amount", lambda t, base, rates: t.get("from_amount") or 0.0
    )
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base: amt)
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {})
    _pin_today(monkeypatch, cashflow)

    get_calls = _count_calls(monkeypatch, CategoryStore, "get")
    query_calls = _count_calls(monkeypatch, TransactionStore, "query_txns")

    rows = cashflow._historical_months("USD", 1, 1000.0)

    assert len(rows) == 1
    assert len(rows[0]["events"]) == 200
    assert len(get_calls) == 0, (
        f"CategoryStore.get() called {len(get_calls)} times labelling 200 transactions — "
        "should build one label map (_category_label_map) and reuse it, not look up per row"
    )
    assert len(query_calls) == 0, (
        f"query_txns() called {len(query_calls)} times inside the per-month loop — "
        "should filter the already-loaded transaction list (_filter_txns), not re-read the store"
    )


def test_scorecard_actual_month_flow_does_not_reread_store_per_month(monkeypatch):
    txns = [_txn(i, category="cat_food", kind="income") for i in range(50)]

    monkeypatch.setattr(fs.ledger_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(fs, "load_fx_cache", lambda: {"USD": 1.0})
    monkeypatch.setattr(
        fs.cashflow_service,
        "build_projection",
        lambda **kw: {"monthly_income_avg": 100.0, "monthly_expenses_avg": 50.0},
    )
    monkeypatch.setattr(fs.cashflow_service, "_rolling_avg_income", lambda base, *_a, **_k: 100.0)
    monkeypatch.setattr(fs.cashflow_service, "_rolling_avg_expenses", lambda base, *_a, **_k: 50.0)
    monkeypatch.setattr(
        fs.ledger_service,
        "net_worth_history",
        lambda *a, **k: [
            {"date": f"2026-{m:02d}-28", "net_worth": 1000.0 + m} for m in range(1, 8)
        ],
    )
    monkeypatch.setattr(fs.ledger_service.transactions, "all", lambda: txns)
    monkeypatch.setattr(
        fs.ledger_service, "txn_base_amount", lambda t, base, rates: t.get("to_amount") or 0.0
    )
    _pin_today(monkeypatch, fs)

    query_calls = _count_calls(monkeypatch, TransactionStore, "query_txns")

    result = fs.scorecard(window="6M")

    assert result["data_quality"]["periods_available"] >= 1
    assert len(query_calls) == 0, (
        f"query_txns() called {len(query_calls)} times across the scored months — "
        "scorecard() should load the ledger once (all_txns) and reuse it per month"
    )


def test_build_projection_reads_the_ledger_once(monkeypatch):
    """The WHOLE projection may read the transaction store once — not once per leg.

    The 2026-07-27 fix above stopped `_historical_months` re-reading inside its own loop, but
    left `build_projection`'s fan-out untouched: every leg still fetched independently. Profiled
    live on 2026-08-11 that was 18 reads for ONE call — `transaction_store.all()` x10 (2743ms)
    plus `query_txns()` x8 (1785ms), 91% of a 4978ms request spent re-parsing the same 2003
    rows. One load is ~85ms; one pass over the result is ~5ms. The work was never the problem.

    `query_txns` is counted as a read because it is NOT a SQL query — it calls `.all()` and
    filters in Python, so each one is a full reparse of the book.

    Bounded by a small constant, never scaling with legs/months, exactly like its neighbours.
    """
    txns = [
        _txn(i, category="cat_food" if i % 2 else "cat_rent", kind="expense") for i in range(60)
    ]
    txns += [_txn(200 + i, category="cat_pay", kind="income") for i in range(20)]
    acct = {
        "id": "bank",
        "kind": "asset",
        "type": "cash",
        "currency": "USD",
        "initial_balance": 0.0,
    }

    monkeypatch.setattr(cashflow.svc.accounts, "query_accounts", lambda *a, **k: [acct])
    monkeypatch.setattr(cashflow.svc.transactions, "all", lambda: list(txns))
    monkeypatch.setattr(
        cashflow.svc, "txn_base_amount", lambda t, base, rates: t.get("from_amount") or 0.0
    )
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base, rates=None: amt)
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {"USD": 1.0})
    monkeypatch.setattr(cashflow, "_load_profile", lambda: {})
    monkeypatch.setattr(cashflow.svc, "base_currency", lambda: "USD")
    _pin_today(monkeypatch, cashflow)

    all_calls = _count_calls(monkeypatch, TransactionStore, "all")
    query_calls = _count_calls(monkeypatch, TransactionStore, "query_txns")

    from backend.db import read_cache

    read_cache.reset()  # a cache HIT would make this pass without proving anything
    cashflow.build_projection(months=12, history_months=6)

    reads = len(all_calls) + len(query_calls)
    assert reads <= 2, (
        f"build_projection made {reads} ledger reads "
        f"(all()={len(all_calls)}, query_txns()={len(query_calls)}) — it should load once and "
        "thread that list into every leg (cash_balance_base, _rolling_avg_*, _classified_baseline, "
        "_historical_months, _current_month_actuals all take an optional pre-loaded list)"
    )


def test_build_projection_folds_each_runway_rung_exactly_once(monkeypatch):
    """Each rung rescans every account's whole history. They were computed once for the starting
    balance and AGAIN, unconditionally, for the response dict — so a projection with both
    toggles on folded the ledger four times where twice would do, and the two folds could
    disagree if a write landed between them.

    The toggle applies to the SUM, never to the figure: the response reports both rungs whatever
    the toggles say (that is the "+S$x" hint beside each one), so a toggle-gated local is what
    forced the second fold."""
    calls = {"fd": 0, "inv": 0}
    real_fd = cashflow.fixed_deposit_base
    real_inv = cashflow.investments_runway_base

    def _fd(base, txns=None):
        calls["fd"] += 1
        return real_fd(base, txns)

    def _inv(base, txns=None):
        calls["inv"] += 1
        return real_inv(base, txns)

    acct = {"id": "bank", "kind": "asset", "type": "cash", "currency": "USD"}
    monkeypatch.setattr(cashflow.svc.accounts, "query_accounts", lambda *a, **k: [acct])
    monkeypatch.setattr(cashflow.svc.transactions, "all", list)
    monkeypatch.setattr(cashflow.svc, "to_base", lambda amt, frm, base, rates=None: amt)
    monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {"USD": 1.0})
    monkeypatch.setattr(cashflow, "_load_profile", lambda: {})
    monkeypatch.setattr(cashflow.svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(cashflow, "fixed_deposit_base", _fd)
    monkeypatch.setattr(cashflow, "investments_runway_base", _inv)
    _pin_today(monkeypatch, cashflow)

    from backend.db import read_cache

    read_cache.reset()
    both_on = cashflow.build_projection(
        months=1, history_months=0, include_fixed_deposits=True, include_investments=True
    )
    assert calls == {"fd": 1, "inv": 1}

    read_cache.reset()
    both_off = cashflow.build_projection(months=1, history_months=0)
    assert calls == {"fd": 2, "inv": 2}
    # Reported either way — only the SUM is gated.
    for proj in (both_on, both_off):
        assert proj["fixed_deposit_base"] == 0.0
        assert proj["investments_base"] == 0.0


def test_scorecard_shares_one_ledger_read_across_all_legs(monkeypatch):
    """2026-08-12: scorecard's legs (projection, both rolling-average probes, net-worth
    history, its own month scan) triggered `transaction_store.all()` FIVE times per request
    — 168ms of a 463ms call re-parsing the same rows. One hoisted load now feeds every leg,
    asserted by IDENTITY (the same list object reaches each seam), not just by count."""
    txns = [_txn(i, category="cat_food", kind="income") for i in range(10)]
    seen: dict[str, object] = {}
    loads: list = []

    monkeypatch.setattr(fs.ledger_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(fs, "load_fx_cache", lambda: {"USD": 1.0})

    def _proj(**kw):
        seen["projection"] = kw.get("all_txns")
        return {"monthly_income_avg": 100.0, "monthly_expenses_avg": 50.0}

    def _inc(base, all_txns=None):
        seen["income"] = all_txns
        return 100.0

    def _exp(base, all_txns=None):
        seen["expenses"] = all_txns
        return 50.0

    def _nwh(all_txns=None):
        seen["history"] = all_txns
        return [{"date": f"2026-{m:02d}-28", "net_worth": 1000.0 + m} for m in range(1, 8)]

    def _all():
        loads.append(1)
        return list(txns)

    monkeypatch.setattr(fs.cashflow_service, "build_projection", _proj)
    monkeypatch.setattr(fs.cashflow_service, "_rolling_avg_income", _inc)
    monkeypatch.setattr(fs.cashflow_service, "_rolling_avg_expenses", _exp)
    monkeypatch.setattr(fs.ledger_service, "net_worth_history", _nwh)
    monkeypatch.setattr(fs.ledger_service.transactions, "all", _all)
    monkeypatch.setattr(
        fs.ledger_service, "txn_base_amount", lambda t, base, rates: t.get("to_amount") or 0.0
    )
    _pin_today(monkeypatch, fs)

    result = fs.scorecard(window="6M")

    assert result["window"] == "6M"
    assert len(loads) == 1  # the ledger was read ONCE for the whole pipeline
    shared = seen["projection"]
    assert shared is not None
    assert seen["income"] is shared  # every leg received THE SAME loaded list…
    assert seen["expenses"] is shared
    assert seen["history"] is shared  # …not its own re-read


def test_list_reviews_skips_the_ledger_when_nothing_is_pending(monkeypatch):
    """2026-10-06: an empty queue — the common case — loaded every transaction just to
    return nothing (0.4–4s per call on a 2.4k-row cloud book)."""
    from backend.services import match_review as mr

    loads: list = []
    monkeypatch.setattr(mr.reviews, "pending", lambda batch_id=None: [])
    monkeypatch.setattr(mr.ledger_service.transactions, "all", lambda: loads.append(1) or [])

    assert mr.list_reviews() == ([], 0)
    assert loads == []


def test_list_reviews_loads_the_ledger_once(monkeypatch):
    """2026-08-12: the zombie sweep and the enrich loop each built their own full-ledger
    id map (2 parses per request). One shared load now feeds both."""
    from backend.services import match_review as mr

    items = [
        {"id": f"r{i}", "incoming_id": f"t{i}", "canonical_id": f"t{i + 1}", "alternatives": []}
        for i in range(5)
    ]
    txns = [_txn(i, category="cat_food") for i in range(10)]
    loads: list = []

    def _all():
        loads.append(1)
        return list(txns)

    monkeypatch.setattr(mr.reviews, "pending", lambda batch_id=None: items)
    monkeypatch.setattr(mr.ledger_service.transactions, "all", _all)

    out, superseded = mr.list_reviews()

    assert len(loads) == 1, f"list_reviews loaded the ledger {len(loads)} times — must be 1"
    assert len(out) + superseded == len(items)
