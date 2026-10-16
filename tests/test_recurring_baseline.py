"""The three-bucket split and how it reaches the projection
(.claude/rfcs/2026-07-27-cashflow-stream-model.md)."""

from __future__ import annotations

from datetime import date

import pytest

from backend.db.recurring_stream_store import RecurringStreamStore
from backend.services import recurring_baseline as rb


def _row(rid, amount, direction="out", payee="p", when="2026-05-10", category="cat_food"):
    return {
        "id": rid,
        "date": date.fromisoformat(when),
        "amount": float(amount),
        "payee_id": payee,
        "category": category,
        "label": rid,
        "direction": direction,
    }


def _months(category, months, direction="out", amount=50.0, per_month=1):
    """`category` occurring once per listed month — a recurring POPULATION."""
    return [
        _row(f"{category}-{m}-{i}", amount, direction, f"p{i}", f"2026-0{m}-1{i}", category)
        for m in months
        for i in range(per_month)
    ]


# ── which categories count as recurring ───────────────────────────────────────────


def test_a_category_present_every_month_recurs():
    rows = _months("cat_meals", [4, 5, 6], per_month=3)
    keep, observed = rb.recurring_categories(rows, "out")
    assert observed == 3
    assert "cat_meals" in keep


def test_a_category_seen_in_ONE_month_is_a_one_off():
    """The live case: a S$5,000 "Pass-through" and a stock sale, each appearing once. Neither
    is big enough to trip a magnitude rule (9.9% and 1.9% of the pool) — but neither recurs."""
    rows = _months("cat_meals", [4, 5, 6], per_month=3) + _months("cat_passthrough", [4])
    keep, _ = rb.recurring_categories(rows, "out")
    assert "cat_meals" in keep
    assert "cat_passthrough" not in keep


def test_income_is_judged_STRICTLY_and_expense_LENIENTLY():
    """Accounting prudence: do not overstate income, do not understate expenses. A category
    present in 2 of 3 months is kept as an expense but dropped as income — the owner's
    Equity Compensation (2/3 months, S$50K) is exactly this case."""
    rows = _months("cat_equity", [5, 6], direction="in") + _months(
        "cat_salary", [4, 5, 6], direction="in"
    )
    keep_in, _ = rb.recurring_categories(rows, "in")
    assert "cat_salary" in keep_in
    assert "cat_equity" not in keep_in

    rows_out = _months("cat_gifts", [5, 6]) + _months("cat_meals", [4, 5, 6])
    keep_out, _ = rb.recurring_categories(rows_out, "out")
    assert {"cat_gifts", "cat_meals"} <= keep_out


def test_uncategorized_is_always_kept():
    """ "We could not classify this" is not evidence of a windfall. Silently dropping money we
    failed to understand is the worse error."""
    # Present in only 1 of 3 months, and income is judged strictly — kept anyway.
    rows = _months(rb.UNCATEGORIZED, [4], direction="in") + _months(
        "cat_salary", [4, 5, 6], direction="in"
    )
    keep, _ = rb.recurring_categories(rows, "in")
    assert rb.UNCATEGORIZED in keep


def test_a_single_observed_month_keeps_everything():
    """A brand-new book: 1/1 months passes any ratio, so a first paycheck is counted rather
    than dismissed as unproven."""
    rows = _months("cat_salary", [6], direction="in")
    keep, observed = rb.recurring_categories(rows, "in")
    assert observed == 1 and "cat_salary" in keep


def test_no_data_yields_no_categories():
    assert rb.recurring_categories([], "in") == (set(), 0)


def test_split_partitions_the_pool_by_category():
    pool = _months("cat_meals", [6], per_month=2) + _months("cat_passthrough", [6])
    kept, one_offs = rb._split_by_coverage(pool, {"cat_meals"})
    assert len(kept) == 2 and len(one_offs) == 1


# ── averaging ─────────────────────────────────────────────────────────────────────


def test_average_divides_by_months_with_data_not_window_width():
    rows = [_row("a", 300, when="2026-05-02"), _row("b", 300, when="2026-05-20")]
    assert rb._monthly_average(rows, 3) == 600.0  # one month of data → 600, not 200


def test_empty_pool_averages_to_zero():
    assert rb._monthly_average([], 3) == 0.0


# ── overlays (confirm / dismiss) ─────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path, monkeypatch, db_isolated):
    """RecurringStreamStore is DB-backed (plan 4a task 3) — db_isolated gives this test
    its own fresh, migrated database."""
    s = RecurringStreamStore()
    monkeypatch.setattr(rb, "RecurringStreamStore", lambda: s)
    return s


def test_overlay_status_roundtrip_and_reset(store):
    store.set_status("rs_abc", "dismissed")
    assert store.overlays()["rs_abc"]["status"] == "dismissed"
    store.set_status("rs_abc", "confirmed")  # idempotent upsert, not a duplicate row
    assert len(store.all()) == 1
    assert store.overlays()["rs_abc"]["status"] == "confirmed"
    assert store.reset("rs_abc") is True
    assert store.overlays() == {}
    assert store.reset("rs_abc") is False


def test_overlay_rejects_an_unknown_status(store):
    with pytest.raises(ValueError, match="^status:"):
        store.set_status("rs_abc", "maybe")
