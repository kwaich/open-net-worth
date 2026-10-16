"""End-to-end: the classified baseline reaches the projection
(.claude/rfcs/2026-07-27-cashflow-stream-model.md).

The headline test reproduces the live defect that opened this work — three equity-sale
proceeds inside the trailing window drove a S$40,000/month "income" baseline against a true
S$7,000 salary, so the cushion chart projected a permanently-climbing balance.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.services import cashflow, recurring_baseline


def _txn(tid, kind, amount, when, payee, desc="", category="cat_default"):
    """A minimal two-leg row shaped like the ledger's own.

    `category` matters: classification asks whether a category recurs across months, so a
    fixture without one lands everything in UNCATEGORIZED (always kept) and can never
    exercise the one-off path.
    """
    leg = "to" if kind == "income" else "from"
    return {
        "id": tid,
        "kind": kind,
        "date": when,
        "description": desc or tid,
        "payee_id": payee,
        f"{leg}_account_id": "acc_1",
        f"{leg}_amount": amount,
        f"{leg}_currency": "SGD",
        "base_amount": amount,
        "base_currency": "SGD",
        "category": category,
    }


def _occurrences(today: date, n: int, day: int):
    """`n` monthly occurrences ending with the most recent one that has ALREADY happened.

    Includes the CURRENT month when that day has passed — which is what a live stream looks
    like. A fixture that stops at last month describes a stream whose latest payment is up to
    ~8 weeks old, which the staleness check correctly writes off as ended.
    """
    start = 0 if day <= today.day else 1
    return [cashflow._sub_months(today.year, today.month, i) for i in range(start, start + n)]


def _salary_months(today: date, n=6):
    """`n` monthly salary payments, the most recent already paid."""
    return [
        _txn(f"sal{i}", "income", 7000.0, f"{y:04d}-{m:02d}-26", "pay_acme", "Salary", "cat_salary")
        for i, (y, m) in enumerate(_occurrences(today, n, 26))
    ]


def _rent_months(today: date, n=6):
    return [
        _txn(f"rent{i}", "expense", 2150.0, f"{y:04d}-{m:02d}-01", "pay_ll", "Rent", "cat_rent")
        for i, (y, m) in enumerate(_occurrences(today, n, 1))
    ]


def _noise(today: date, count=12):
    """Small everyday spending — the variable baseline, which must SURVIVE. Spread across all
    three residual months so "Daily meals" reads as a recurring population, which is exactly
    what makes averaging it legitimate."""
    out = []
    for i in range(count):
        y, m = cashflow._sub_months(today.year, today.month, (i % 3) + 1)
        out.append(
            _txn(
                f"n{i}",
                "expense",
                30.0,
                f"{y:04d}-{m:02d}-{(i % 27) + 1:02d}",
                f"pay_shop{i}",
                "Meal",
                "cat_meals",
            )
        )
    return out


@pytest.fixture()
def book(monkeypatch, tmp_path, db_isolated):
    """Wire a synthetic ledger + an isolated stream store into both services.
    RecurringStreamStore is DB-backed (plan 4a task 3) — db_isolated gives this test its
    own fresh, migrated database."""
    from backend.db.recurring_stream_store import RecurringStreamStore

    store = RecurringStreamStore()
    monkeypatch.setattr(recurring_baseline, "RecurringStreamStore", lambda: store)

    def install(txns):
        by_kind = {}
        for t in txns:
            by_kind.setdefault(t["kind"], []).append(t)

        def query_txns(kind=None, date_from=None, date_to=None, **kw):
            rows = by_kind.get(kind, [])
            return [
                t
                for t in rows
                if (not date_from or t["date"] >= date_from)
                and (not date_to or t["date"] <= date_to)
            ]

        monkeypatch.setattr(recurring_baseline.svc.transactions, "query_txns", query_txns)
        monkeypatch.setattr(cashflow.svc.transactions, "query_txns", query_txns)
        # `.all()` is now the single source: build_projection loads the book ONCE and threads
        # that list into every leg (see test_cashflow_query_efficiency), so a fixture that only
        # stubs query_txns would starve the code under test. Stubbing both keeps the two access
        # paths agreeing, which is what production does — query_txns is itself `.all()` plus an
        # in-memory filter.
        monkeypatch.setattr(cashflow.svc.transactions, "all", lambda **kw: list(txns))
        monkeypatch.setattr(recurring_baseline.svc.transactions, "all", lambda **kw: list(txns))
        monkeypatch.setattr(
            recurring_baseline.svc, "txn_base_amount", lambda t, b, r: t["base_amount"]
        )
        monkeypatch.setattr(cashflow.svc, "txn_base_amount", lambda t, b, r: t["base_amount"])
        monkeypatch.setattr(cashflow, "load_fx_cache", lambda: {"SGD": 1.0})
        monkeypatch.setattr(
            recurring_baseline, "load_fx_cache", lambda: {"SGD": 1.0}, raising=False
        )
        return store

    return install


def _baseline(today=None):
    return recurring_baseline.resolve_baseline("SGD", {"SGD": 1.0}, today or date.today())


# ── the headline regression ───────────────────────────────────────────────────────


def test_one_off_proceeds_do_not_inflate_the_recurring_income_baseline(book):
    """THE bug. Three equity-sale proceeds land in the window alongside a real salary. Before
    the stream model, the flat average reported ~4x the true recurring income and projected it
    forward for 40 months."""
    today = date.today()
    y, m = cashflow._sub_months(today.year, today.month, 1)
    txns = _salary_months(today) + _noise(today)
    txns += [
        _txn(
            "w1",
            "income",
            35291.88,
            f"{y:04d}-{m:02d}-06",
            "pay_acme",
            "Company stocks sale",
            "cat_equity",
        ),
        _txn(
            "w2",
            "income",
            15024.72,
            f"{y:04d}-{m:02d}-18",
            "pay_acme",
            "Stock sale proceeds",
            "cat_equity",
        ),
        _txn(
            "w3",
            "income",
            28439.50,
            f"{y:04d}-{m:02d}-04",
            "pay_acme",
            "Stock sale proceeds",
            "cat_equity",
        ),
    ]
    book(txns)

    result = _baseline(today)
    salary = [s for s in result["streams"] if s["amount_base"] == 7000.0]
    assert salary, "the real salary must still be detected as a stream"

    excluded = {o["label"] for o in result["one_offs"]}
    assert {"Company stocks sale", "Stock sale proceeds"} & excluded

    # The projected monthly income is the salary, NOT salary + smeared windfalls.
    projected = salary[0]["monthly_equivalent"] + result["variable_in"]
    assert projected == pytest.approx(7000.0, abs=2000), projected


def test_a_brand_new_users_first_paycheck_is_not_excluded_as_a_windfall(book):
    """The first-paycheck guard. One paycheck among a few refunds dominates its own pool by
    definition, so the share rule alone would exclude it and project ZERO income. With no
    stream on that side to carry the flow, the exclusion is reverted — 'unproven' is not a
    reason to tell someone they have no income."""
    today = date.today()
    y, m = cashflow._sub_months(today.year, today.month, 1)
    txns = [
        _txn(
            "first", "income", 7000.0, f"{y:04d}-{m:02d}-26", "pay_new_job", "Salary", "cat_salary"
        ),
        _txn("r1", "income", 12.0, f"{y:04d}-{m:02d}-04", "pay_shop", "Refund", "cat_refund"),
        _txn("r2", "income", 8.0, f"{y:04d}-{m:02d}-09", "pay_shop2", "Refund", "cat_refund"),
    ] + _noise(today)
    book(txns)
    result = _baseline(today)
    assert not [s for s in result["streams"] if s["direction"] == "in"]
    assert result["variable_in"] > 6000, "the only income must still be counted"
    assert not [o for o in result["one_offs"] if o["id"] == "first"]


def test_everyday_spending_still_counts_toward_the_forecast(book):
    """The bucket that makes 'only count recurring things' wrong — if variable spend were
    dropped, the forecast would understate the month badly."""
    today = date.today()
    book(_salary_months(today) + _rent_months(today) + _noise(today, count=12))
    result = _baseline(today)
    assert result["variable_out"] > 0, "irregular everyday spend must not be discarded"


def test_streams_land_on_their_own_months_in_the_projection(book):
    """A stream appears as its OWN named event, not folded into one opaque blob."""
    today = date.today()
    book(_salary_months(today) + _rent_months(today) + _noise(today))
    proj = cashflow.build_projection(months=3, history_months=0)
    assert proj["classified"] is True
    future = [mo for mo in proj["months"] if not mo["is_current"]]
    labels = {e["label"] for mo in future for e in mo["events"]}
    assert "Rent" in labels and "Salary" in labels


def _pin_today(monkeypatch, fixed: date):
    class _Pinned(date):
        @classmethod
        def today(cls):
            return fixed

    monkeypatch.setattr(cashflow, "user_today", _Pinned.today)


def test_current_month_stream_event_is_not_scaled_by_days_remaining(book, monkeypatch):
    """Live regression: 'Recurring · rest of month' showed Salary at ~65% of its real amount
    when viewed mid-month. Root cause was the current-month branch scaling every named stream
    event by `remaining_days / days_in_month` — a factor meant only for the averaged variable
    baseline — even though the salary (due the 26th, viewed on the 11th) hadn't posted yet and
    isn't an average at all. A not-yet-posted occurrence must land at its full dated amount."""
    today = date(2026, 7, 11)
    _pin_today(monkeypatch, today)
    book(_salary_months(today) + _noise(today))

    proj = cashflow.build_projection(months=1, history_months=0)
    [current] = proj["months"]
    assert current["is_current"] is True

    recurring_events = {e["label"]: e for e in current["events"] if e["status"] == "recurring"}
    assert recurring_events["Salary"]["amount_base"] == pytest.approx(7000.0)


def test_current_month_stream_event_already_posted_is_not_double_counted(book, monkeypatch):
    """The flip side of the same fix: an occurrence that ALREADY posted this month is already
    in the real actuals `_current_month_actuals` folds in — it must not also appear as a
    projected 'recurring' event, or the month's total double-counts it."""
    today = date(2026, 7, 11)
    _pin_today(monkeypatch, today)
    book(_rent_months(today) + _noise(today))

    proj = cashflow.build_projection(months=1, history_months=0)
    [current] = proj["months"]

    actual_labels = {e["label"] for e in current["events"] if e["status"] == "actual"}
    recurring_labels = {e["label"] for e in current["events"] if e["status"] == "recurring"}
    assert "Rent" in actual_labels
    assert "Rent" not in recurring_labels


def test_an_empty_book_still_falls_back_to_the_profile_figures(book):
    """No transactions at all → nothing to classify; the profile fallback is untouched."""
    book([])
    proj = cashflow.build_projection(months=2, history_months=0)
    assert proj["baseline_source"] == "profile"
    assert proj["classified"] is False


def test_classification_failure_degrades_to_the_flat_average(book, monkeypatch):
    """Fail-open: a broken classifier must not take every screen that reads /api/cashflow
    down with it."""
    today = date.today()
    book(_salary_months(today) + _noise(today))

    def boom(*a, **kw):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(recurring_baseline, "resolve_baseline", boom)
    proj = cashflow.build_projection(months=2, history_months=0)
    assert proj["classified"] is False
    assert proj["monthly_income_avg"] == proj["actual_income_avg"]


# ── user corrections change the projection ────────────────────────────────────────


def test_dismissing_a_stream_removes_it_from_the_projection(book):
    today = date.today()
    store = book(_salary_months(today) + _rent_months(today) + _noise(today))
    before = _baseline(today)
    rent = next(s for s in before["streams"] if s["amount_base"] == 2150.0)

    store.set_status(rent["key"], "dismissed")
    after = _baseline(today)
    assert rent["key"] not in {s["key"] for s in after["streams"]}
    assert rent["key"] in {s["key"] for s in after["candidates"]}


def test_confirming_a_tentative_stream_promotes_it_into_the_projection(book):
    """The escape hatch for a new job paid only twice — the user confirms, and it counts."""
    today = date.today()
    store = book(_salary_months(today, n=2) + _noise(today))
    before = _baseline(today)
    tentative = next(s for s in before["candidates"] if s["confidence"] == "tentative")
    assert tentative["key"] not in {s["key"] for s in before["streams"]}

    store.set_status(tentative["key"], "confirmed")
    after = _baseline(today)
    assert tentative["key"] in {s["key"] for s in after["streams"]}


# ── declared streams are retired (ADR 2026-10-16) ────────────────────────────────


def _tenant_months(today: date, n=4):
    return [
        _txn(
            f"ten{i}",
            "income",
            5300.0,
            f"{y:04d}-{m:02d}-05",
            "pay_tenant",
            "Rent in",
            "cat_rental",
        )
        for i, (y, m) in enumerate(_occurrences(today, n, 5))
    ]


def _other_salary_months(today: date, n=4):
    return [
        _txn(f"oth{i}", "income", 5400.0, f"{y:04d}-{m:02d}-26", "pay_acme", "Salary", "cat_salary")
        for i, (y, m) in enumerate(_occurrences(today, n, 26))
    ]


def test_independent_income_streams_of_close_amounts_both_project(book):
    """The launch blocker: a declaration used to absorb history on amount alone, so a S$5,400
    salary swallowed a tenant's S$5,300 rent. Detection keys on payee, so both stay."""
    today = date.today()
    book(_tenant_months(today) + _other_salary_months(today) + _noise(today))
    result = _baseline(today)
    income = {s["amount_base"]: s for s in result["streams"] if s["direction"] == "in"}
    assert set(income) == {5300.0, 5400.0}
    assert all(s["source"] != "declared" for s in result["streams"])
    assert result["variable_in"] == 0.0
    assert result["one_offs"] == []

    proj = cashflow.build_projection(months=4, history_months=0)
    future = [mo for mo in proj["months"] if not mo["is_current"]]
    assert future and all(mo["total_in"] == pytest.approx(10700.0) for mo in future)


def test_a_dormant_declared_row_feeds_nothing(book):
    """Rows written before the concept was retired stay in the table but nothing reads them."""
    today = date.today()
    store = book(_tenant_months(today) + _noise(today))
    before = _baseline(today)

    y, m = cashflow._add_months(today.year, today.month, 1)
    store.insert(
        {
            "type": "declared",
            "id": "rst_old",
            "direction": "in",
            "label": "Salary",
            "amount": 5400.0,
            "cadence": "monthly",
            "anchor_date": f"{y:04d}-{m:02d}-01",
        }
    )
    after = _baseline(today)
    assert after == before
    assert not recurring_baseline.upcoming_income(60, "SGD", {"SGD": 1.0}, today) == []
    assert all(
        e["label"] != "Salary"
        for e in recurring_baseline.upcoming_income(60, "SGD", {"SGD": 1.0}, today)
    )


def test_there_is_no_tool_to_declare_a_stream():
    from backend.ai.tools.registry import registered_tool_names

    names = set(registered_tool_names())
    assert "declare_recurring_stream" not in names
    assert "remove_recurring_streams" not in names
    assert {"get_recurring_streams", "set_recurring_status"} <= names


def test_a_forecast_row_is_named_by_its_payee_and_shows_a_rename(monkeypatch):
    """The payee keeps the bank's wording until the user renames it; the forecast shows
    whichever it holds, never a copy cleaned of its numbers (owner, 2026-10-15)."""
    names = {"pay_mum": "Mum", "pay_paynow": "PayNow 91234567"}
    monkeypatch.setattr(recurring_baseline.payee_registry, "payee_names", lambda: names)
    txns = [
        _txn("a", "income", 500.0, "2026-09-01", "pay_mum", "PAYNOW FROM 98765432 REF8800412239"),
        _txn("b", "income", 300.0, "2026-09-02", "pay_paynow", "PayNow 91234567"),
        _txn("c", "income", 100.0, "2026-09-03", None, "GIRO 55501234"),
    ]
    rows = recurring_baseline._load_rows("2026-09-01", "2026-09-30", "SGD", {}, all_txns=txns)
    assert [r["label"] for r in rows] == ["Mum", "PayNow 91234567", "GIRO 55501234"]
