"""Recurring-stream detection — the pure math (.claude/rfcs/2026-07-27-cashflow-stream-model.md).

These tests pin the behaviours that four earlier, rejected designs got wrong. Each one names
the failure it guards against, because every one of them looked correct until measured against
a real book.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.services import recurring


def _row(rid, when, amount, payee, direction="out", label=None):
    return {
        "id": rid,
        "date": when if isinstance(when, date) else date.fromisoformat(when),
        "amount": float(amount),
        "payee_id": payee,
        "label": label or f"{payee} txn",
        "direction": direction,
    }


def _monthly(payee, amount, months, *, direction="out", day=15, start_year=2026, label=None):
    """`months` occurrences on the same day-of-month, walking backwards from 2026-06."""
    rows = []
    for i in range(months):
        y, m = recurring._add_months(start_year, 6, -i)
        rows.append(_row(f"{payee}-{i}", date(y, m, day), amount, payee, direction, label))
    return rows


# ── what IS a stream ──────────────────────────────────────────────────────────────


def test_three_regular_occurrences_are_a_matured_stream():
    streams = recurring.detect_streams(_monthly("pay_rent", 2150, 3))
    assert len(streams) == 1
    assert streams[0]["cadence"] == "monthly"
    assert streams[0]["amount_base"] == 2150
    assert streams[0]["occurrences"] == 3
    assert streams[0]["confidence"] == "matured"


def test_two_occurrences_are_tentative_not_matured():
    """2 hits is as often coincidence as pattern — surfaced, never silently projected."""
    streams = recurring.detect_streams(_monthly("pay_gym", 60, 2))
    assert [s["confidence"] for s in streams] == ["tentative"]


def test_a_single_occurrence_is_never_a_stream():
    assert recurring.detect_streams(_monthly("pay_once", 5000, 1)) == []


def test_small_price_rise_stays_one_stream():
    """A $9.99 → $10.99 subscription hike must not split into two half-streams."""
    rows = [
        _row("a", "2026-04-10", 9.99, "pay_netflix"),
        _row("b", "2026-05-10", 10.20, "pay_netflix"),
        _row("c", "2026-06-10", 10.35, "pay_netflix"),
    ]
    streams = recurring.detect_streams(rows)
    assert len(streams) == 1
    assert streams[0]["occurrences"] == 3


@pytest.mark.parametrize(
    "step_days,expected",
    [(7, "weekly"), (14, "biweekly"), (91, "quarterly"), (365, "annual")],
)
def test_non_monthly_cadences_classify(step_days, expected):
    anchor = date(2026, 1, 5)
    rows = [_row(f"r{i}", anchor + timedelta(days=step_days * i), 100, "pay_x") for i in range(4)]
    streams = recurring.detect_streams(rows)
    assert [s["cadence"] for s in streams] == [expected]


# ── what is NOT a stream (the false-positive guards) ──────────────────────────────


def test_frequent_but_irregular_spend_never_becomes_a_projected_stream():
    """THE false-positive guard: daily-ish coffee at one cafe repeats often but at wildly
    irregular gaps and amounts. Frequency is not the signal — interval REGULARITY is (the
    distinction Plaid's production detector draws, and the reason groceries/coffee must fall
    through to the variable baseline instead of becoming fake 'obligations').

    Two of those visits DO happen to be ~30 days apart at a similar price, so pure cadence
    maths pairs them. That is contained, not eliminated: the pair has 2 occurrences, so it
    stays `tentative` and is never projected — only a user confirming it promotes it. A
    "coverage gate" that suppressed it outright was tried and reverted (see detect_streams:
    it also killed a real salary whose payee sent one-off proceeds too)."""
    rows = [
        _row("c1", "2026-04-02", 4.50, "pay_cafe"),
        _row("c2", "2026-04-03", 5.20, "pay_cafe"),
        _row("c3", "2026-04-09", 4.10, "pay_cafe"),
        _row("c4", "2026-04-21", 6.80, "pay_cafe"),
        _row("c5", "2026-05-02", 4.40, "pay_cafe"),
    ]
    assert [s["confidence"] for s in recurring.detect_streams(rows)] in ([], ["tentative"])
    assert not [s for s in recurring.detect_streams(rows) if s["confidence"] == "matured"]


def test_same_amount_at_irregular_intervals_is_not_a_stream():
    rows = [
        _row("x1", "2026-01-03", 500, "pay_y"),
        _row("x2", "2026-02-19", 500, "pay_y"),
        _row("x3", "2026-06-27", 500, "pay_y"),
    ]
    assert recurring.detect_streams(rows) == []


def test_transactions_without_a_payee_are_never_streams():
    rows = [_row(f"n{i}", date(2026, 6 - i, 10), 100, "") for i in range(3)]
    assert recurring.detect_streams(rows) == []


def test_one_payee_can_carry_both_a_salary_stream_and_an_unrelated_one_off():
    """The owner's own counter-example: *"the payer can send me 5000 one time bonus"*.
    Recurrence is a property of a STREAM, not of a counterparty — the salary must be detected
    and the same-payee windfall must NOT be swept in with it."""
    rows = _monthly("pay_acme", 9000, 3, direction="in", label="Salary")
    rows.append(_row("bonus", "2026-05-20", 5000, "pay_acme", "in", "One-time bonus"))
    streams = recurring.detect_streams(rows)
    assert len(streams) == 1
    assert streams[0]["amount_base"] == 9000
    assert "bonus" not in streams[0]["txn_ids"]


def test_one_payee_with_TWO_genuine_streams_keeps_both():
    """A landlord charging rent on the 1st AND a separate fee on the 15th is two real streams —
    neither may veto the other just because each is only half of that payee's traffic."""
    rows = _monthly("pay_landlord", 2150, 3, day=1) + _monthly("pay_landlord", 180, 3, day=15)
    streams = recurring.detect_streams(rows)
    assert sorted(s["amount_base"] for s in streams) == [180, 2150]


def test_same_payee_same_day_rows_merge_into_one_event():
    """A single real payment split across ledger rows must recombine — the reference case being a
    Meridian Bank instalment booked as 1,175.65 + 244.35 on one day, which is the 1,420.00 charged
    every other month. Per-row clustering saw only the fragment and reported a shrinking bill.

    Accepted trade-off: two genuinely separate charges from one payee on the SAME DAY also
    merge. The projected total is identical either way (it is the same money leaving on the
    same day), so this costs itemisation detail, never accuracy — and no data distinguishes a
    split booking from two same-day charges.
    """
    rows = []
    for i in range(3):
        y, m = recurring._add_months(2026, 6, -i)
        rows.append(_row(f"a{i}", date(y, m, 5), 1175.65, "pay_meridian"))
        rows.append(_row(f"b{i}", date(y, m, 5), 244.35, "pay_meridian"))
    streams = recurring.detect_streams(rows)
    assert len(streams) == 1
    assert streams[0]["amount_base"] == pytest.approx(1420.00)
    assert streams[0]["occurrences"] == 3
    assert len(streams[0]["txn_ids"]) == 6  # both fragments stay claimed


def test_stream_projects_the_LATEST_amount_not_the_mean():
    """A projection is a claim about what happens next. The mean permanently lags a moving
    amount — a 9.99 → 10.99 subscription would project 10.49 forever."""
    rows = [
        _row("s1", "2026-04-10", 9.99, "pay_netflix"),
        _row("s2", "2026-05-10", 10.20, "pay_netflix"),
        _row("s3", "2026-06-10", 10.35, "pay_netflix"),
    ]
    [stream] = recurring.detect_streams(rows)
    assert stream["amount_base"] == pytest.approx(10.35)  # what next month will cost
    assert stream["average_amount_base"] == pytest.approx(10.18, abs=0.01)  # lags behind


def test_a_price_jump_outside_tolerance_splits_rather_than_averaging_the_two():
    """±5% is what holds a drifting stream together; a bigger jump (a real raise, a repricing)
    starts a SEPARATE cluster instead of silently averaging old and new into a figure that was
    never charged. The old cluster then ages out via `ended`."""
    rows = [
        _row("s1", "2026-04-10", 10.00, "pay_x"),
        _row("s2", "2026-05-10", 10.00, "pay_x"),
        _row("s3", "2026-06-10", 20.00, "pay_x"),
    ]
    amounts = sorted(s["amount_base"] for s in recurring.detect_streams(rows))
    assert 15.0 not in amounts  # never invents a midpoint nobody was charged


def test_a_stream_that_stopped_is_marked_ended():
    """A pay rise starts a NEW cluster (9% is outside the ±5% tolerance), so without this the
    superseded salary keeps projecting on the strength of its old history."""
    old = _monthly("pay_x", 9000, 8, direction="in")[3:]  # last occurrence ~4 months ago
    streams = recurring.detect_streams(old, today=date(2026, 10, 1))
    assert all(s["ended"] for s in streams)


def test_a_live_stream_is_not_marked_ended():
    rows = _monthly("pay_x", 9000, 3, direction="in")
    streams = recurring.detect_streams(rows, today=date(2026, 7, 5))
    assert not any(s["ended"] for s in streams)


def test_a_salary_is_still_matured_when_its_payee_also_sends_one_off_proceeds():
    """The regression the reverted coverage gate caused, pinned so it cannot come back.
    An employer pays a monthly salary AND several one-off equity-sale proceeds; the salary is
    a MATURED stream regardless of how much other traffic that payee has."""
    rows = _monthly("pay_acme", 9000, 3, direction="in", label="Salary")
    rows += [
        _row("s1", "2026-05-06", 35291.88, "pay_acme", "in", "Company stocks sale"),
        _row("s2", "2026-05-18", 15024.72, "pay_acme", "in", "Stock sale proceeds"),
        _row("s3", "2026-06-04", 28439.50, "pay_acme", "in", "Stock sale proceeds"),
        _row("s4", "2026-05-22", 4272.67, "pay_acme", "in", "Salary MEPS"),
        _row("s5", "2026-06-13", 1826.28, "pay_acme", "in", "META sale"),
    ]
    matured = [s for s in recurring.detect_streams(rows) if s["confidence"] == "matured"]
    assert [s["amount_base"] for s in matured] == [9000]


def test_income_and_expense_from_one_payee_do_not_merge():
    rows = _monthly("pay_x", 100, 3, direction="in") + _monthly("pay_x", 100, 3, direction="out")
    assert sorted(s["direction"] for s in recurring.detect_streams(rows)) == ["in", "out"]


# ── projection: streams land on their OWN months, never smoothed ──────────────────


def test_annual_stream_lands_in_one_month_not_spread_across_twelve():
    """The 'do not smooth lumpy items' rule from direct-method practice: an annual premium
    belongs in its own month, not as 1/12th of itself every month."""
    stream = {
        "anchor_date": "2026-03-20",
        "cadence": "annual",
        "amount_base": 1200,
        "direction": "out",
    }
    got = recurring.stream_month_amounts(stream, "2026-07", 18)
    assert got == {"2027-03": 1200}


def test_monthly_stream_lands_every_month_keeping_its_day():
    stream = {
        "anchor_date": "2026-01-31",
        "cadence": "monthly",
        "amount_base": 100,
        "direction": "out",
    }
    got = recurring.stream_month_amounts(stream, "2026-07", 3)
    assert sorted(got) == ["2026-07", "2026-08", "2026-09"]
    assert set(got.values()) == {100}


def test_weekly_stream_lands_four_or_five_times_a_month():
    stream = {
        "anchor_date": "2026-07-01",
        "cadence": "weekly",
        "amount_base": 10,
        "direction": "out",
    }
    got = recurring.stream_month_amounts(stream, "2026-07", 2)
    assert all(v in (40, 50) for v in got.values()), got


def test_today_cutoff_excludes_an_already_posted_occurrence_this_month():
    """An occurrence on/before `today` already posted — it's in the real transactions the
    caller folds into the same month as actuals, so it must not ALSO project here
    (double-booking). Regression: this is what a linear days-remaining prorate was standing
    in for, incorrectly, before `today` became an explicit cutoff."""
    stream = {
        "anchor_date": "2026-07-05",
        "cadence": "monthly",
        "amount_base": 100,
        "direction": "out",
    }
    got = recurring.stream_month_amounts(stream, "2026-07", 1, today=date(2026, 7, 11))
    assert got == {}


def test_today_cutoff_keeps_a_not_yet_posted_occurrence_at_full_amount():
    """An occurrence still ahead of `today` hasn't posted yet — it must land at its full
    dated amount, never scaled by days-remaining (the 'never smoothed' rule). This is the
    live bug: a salary due on the 26th was showing ~65% of its real amount when viewed on
    the 11th."""
    stream = {
        "anchor_date": "2026-06-26",
        "cadence": "monthly",
        "amount_base": 9000,
        "direction": "in",
    }
    got = recurring.stream_month_amounts(stream, "2026-07", 1, today=date(2026, 7, 11))
    assert got == {"2026-07": 9000}


def test_signature_is_stable_across_a_small_price_rise():
    """The overlay key must survive a price change, or a user's dismissal silently detaches."""
    a = recurring._signature("out", "pay_netflix", "monthly", 9.99)
    b = recurring._signature("out", "pay_netflix", "monthly", 10.20)
    assert a == b


# ── same-day merging must not fuse UNRELATED money (2026-08-23) ────────────────


def test_an_unrelated_same_day_payment_does_not_break_a_recurring_stream():
    """A regression case, 2026-08-23. A monthly S$1,500.00 PayNow loan repayment landed
    on 28 Jul alongside an unrelated S$1,800 transfer from the SAME payer. Blind same-day
    summing made that month 3,300.00 — outside the ±5% band — so July fell out of the cluster,
    the stream's last occurrence became 27 Jun, `_is_stale` marked it ended, and a live
    monthly INFLOW stopped being projected at all. The matching S$1,500.00 monthly OUTFLOW it
    reimburses kept projecting, so free cash flow read S$1,500/mo worse than reality.
    """
    rows = [
        _row("apr", date(2026, 4, 28), 1500.00, "pay_relative", "in"),
        _row("may", date(2026, 5, 28), 1500.00, "pay_relative", "in"),
        _row("jun", date(2026, 6, 27), 1500.00, "pay_relative", "in"),
        _row("jul", date(2026, 7, 28), 1500.00, "pay_relative", "in"),
        _row("jul-unrelated", date(2026, 7, 28), 1800.00, "pay_relative", "in"),
    ]
    monthly = [
        s
        for s in recurring.detect_streams(rows, today=date(2026, 8, 23))
        if s["cadence"] == "monthly"
    ]
    assert len(monthly) == 1
    assert monthly[0]["amount_base"] == pytest.approx(1500.00)
    assert monthly[0]["occurrences"] == 4
    assert monthly[0]["ended"] is False


# ── a bill whose amount moves every month ─────────────────────────────────────────


def test_a_monthly_bill_whose_amount_varies_is_one_matured_stream():
    """Owner, 2026-10-06: "a bill can't be 100% the same every month". Cloud hosting billed on
    the 2nd at 254 / 262 / 227 / 197 / 192 / 94 split into ±5% pairs and never matured, so it
    read as day-to-day spending — including a separate small charge between two bills."""
    amounts = [254.40, 262.29, 226.70, 196.59, 191.97, 93.93]
    rows = [_row(f"aws{i}", date(2026, 4 + i, 2), a, "pay_cloud") for i, a in enumerate(amounts)]
    rows.append(_row("extra", "2026-04-10", 6.19, "pay_cloud"))

    streams = recurring.detect_streams(rows, today=date(2026, 9, 10))

    assert len(streams) == 1
    bill = streams[0]
    assert (bill["cadence"], bill["confidence"], bill["occurrences"]) == ("monthly", "matured", 6)
    assert bill["amount_varies"] is True
    assert bill["amount_base"] == round(sum(amounts) / 6, 2)  # the average, not the latest 93.93
    assert "extra" not in bill["txn_ids"]  # the odd extra charge is not the bill


def test_a_frequent_merchant_is_not_a_bill_because_three_visits_fell_a_month_apart():
    """A travel site booked often, three bookings of which happen to land ~30 days apart."""
    visits = ["03-28", "04-20", "04-26", "05-05", "06-02", "06-02", "07-02", "07-14", "08-13"]
    rows = [_row(f"t{i}", f"2026-{d}", 100 + 37 * i, "pay_travel") for i, d in enumerate(visits)]
    assert not [s for s in recurring.detect_streams(rows) if s["confidence"] == "matured"]


def test_varying_amounts_at_irregular_gaps_are_not_a_bill():
    rows = [
        _row("v1", "2026-01-05", 80, "pay_shop"),
        _row("v2", "2026-02-20", 140, "pay_shop"),
        _row("v3", "2026-03-09", 60, "pay_shop"),
        _row("v4", "2026-05-30", 110, "pay_shop"),
    ]
    assert recurring.detect_streams(rows) == []


def test_varying_income_is_left_to_the_steady_rules():
    """Only bills (out) take the varying path: a projected income that never arrives is the
    expensive mistake, so income still needs a steady amount."""
    amounts = [3000, 4200, 2600, 5100]
    rows = [
        _row(f"f{i}", date(2026, 3 + i, 28), a, "pay_client", direction="in")
        for i, a in enumerate(amounts)
    ]
    assert not [s for s in recurring.detect_streams(rows) if s["confidence"] == "matured"]


def test_three_purchases_about_a_month_apart_on_different_days_are_not_a_bill():
    """Review 2026-10-06: three hardware-store runs 25-35 days apart, any amounts."""
    rows = [
        _row("h1", "2026-03-04", 89.0, "pay_hardware"),
        _row("h2", "2026-04-02", 212.0, "pay_hardware"),
        _row("h3", "2026-05-06", 45.0, "pay_hardware"),
    ]
    assert not [s for s in recurring.detect_streams(rows) if s["confidence"] == "matured"]


def test_a_payee_with_hundreds_of_visits_is_rejected_quickly():
    import time

    rows = [
        _row(f"r{i}", date(2026, 1, 1) + timedelta(days=i // 2), 10 + i % 7, "pay_rides")
        for i in range(700)
    ]
    started = time.perf_counter()
    recurring.detect_streams(rows)
    assert time.perf_counter() - started < 2.0
