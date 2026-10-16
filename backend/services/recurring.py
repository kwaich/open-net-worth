"""
recurring — recurring cash-flow STREAM detection (.claude/rfcs/2026-07-27-cashflow-stream-model.md).

The problem this exists to fix: ``cashflow`` used to derive its entire forward baseline from
one blind mean over every ``kind=income``/``kind=expense`` transaction in a trailing 3-month
window, then repeat that number for up to 40 months. Nothing in that computation asked whether
a transaction was actually *recurring*, so a one-off — a stock-sale proceed, a wedding, a
one-time repair — was extrapolated forward forever. (Illustrative repro: S$42,000/mo "income"
against a true S$7,500 salary, because three equity-sale proceeds landed in the window.)

The fix is not a cleverer average. It is recognising that a ledger contains **three kinds of
cash flow**, each needing a *different* treatment:

  1. **Recurring streams** — salary, rent, subscriptions, insurance, a quarterly dividend.
     Regular payee + regular amount + regular interval. These land on THEIR OWN calendar
     months at their own amount, never smoothed into a flat monthly rate (an annual December
     bonus belongs in December, not spread 1/12th across the year).
  2. **Variable baseline** — groceries, transport, coffee, small shopping. Individually
     unpredictable, collectively a real and reasonably stable monthly outflow. These are NOT
     recurring streams (Plaid's production detector deliberately excludes habitual-but-
     irregular spend for exactly this reason: frequency is not the signal, interval
     REGULARITY is) — but a forecast that dropped them would badly understate spending. So
     they are averaged: the old mechanism, applied only where it is actually correct.
  3. **One-off / non-operating** — the stock sale, the inheritance, the one-time repair. Real
     money, fully visible in history, already reflected in the starting balance — but with no
     basis to project forward, so excluded from the baseline entirely.

**Order matters, and is the reason bucket 3 is detectable at all.** An outlier test over the
RAW transaction pool fails: measured against a representative book it flagged the S$7,500
salary as an outlier (it dwarfs dozens of small refunds) and collapsed the baseline to
S$5,200. Extract
streams FIRST and the remainder is genuinely "many small irregular items", where an outlier
test is both safe and meaningful.

Detection is PURE and never persisted — recomputed on every read, so it self-corrects as
transactions arrive. Only user CORRECTIONS (confirm/dismiss) and DECLARED streams live in
``db/recurring_stream_store``. Reads ledger + fx only; no api/ai import (Contract 3).
"""

from __future__ import annotations

import hashlib
from calendar import monthrange
from datetime import date, timedelta
from itertools import combinations

from backend.core.user_time import user_today

# ── tuning constants (named, not magic numbers — tune against real books) ──────────
DETECTION_MONTHS = 12  # trailing full months scanned for streams (need ≥3 hits of a monthly)
AMOUNT_TOLERANCE = 0.05  # ±5% still one stream, so a $9.99→$10.99 price rise stays together
MATURED_OCCURRENCES = 3  # Plaid's production threshold for a "matured" stream
TENTATIVE_OCCURRENCES = 2  # 2 hits = a candidate we surface but do NOT project on
# A stream that has missed this multiple of its own cadence has ENDED — see `_is_stale`.
STALE_AFTER_PERIODS = 1.5

# Cadence windows: (min_days, max_days, months_step, days_step, periods_per_month).
# months_step drives CALENDAR stepping (a monthly stream keeps its day-of-month);
# days_step drives fixed-interval stepping (weekly/biweekly have no calendar anchor).
CADENCES: dict[str, tuple[int, int, int, int, float]] = {
    "weekly": (6, 8, 0, 7, 52 / 12),
    "biweekly": (12, 16, 0, 14, 26 / 12),
    "monthly": (26, 35, 1, 0, 1.0),
    "quarterly": (85, 95, 3, 0, 1 / 3),
    "annual": (350, 380, 12, 0, 1 / 12),
}


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    month += n
    year += (month - 1) // 12
    return year, (month - 1) % 12 + 1


def _clamp_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def _parse(d: str | None) -> date | None:
    try:
        return date.fromisoformat(str(d)[:10])
    except (TypeError, ValueError):
        return None


# ── detection ─────────────────────────────────────────────────────────────────────


def _cluster_by_amount(rows: list[dict]) -> list[list[dict]]:
    """Greedy amount clustering within ±AMOUNT_TOLERANCE of the cluster's running mean.

    Sorted ascending first so the walk is stable and a price rise chains into one cluster
    rather than splitting on arrival order.
    """
    clusters: list[list[dict]] = []
    for row in sorted(rows, key=lambda r: r["amount"]):
        placed = False
        for cluster in clusters:
            mean = sum(r["amount"] for r in cluster) / len(cluster)
            if mean > 0 and abs(row["amount"] - mean) / mean <= AMOUNT_TOLERANCE:
                cluster.append(row)
                placed = True
                break
        if not placed:
            clusters.append([row])
    return clusters


# A same-day group larger than this skips the subset search — the combinatorics are not
# worth it, and a payee booking seven rows on one day is not the split-booking case.
_MAX_SAME_DAY_SUBSET = 6


def _matches_a_known_amount(amount: float, anchors: list[float]) -> bool:
    """Is `amount` within the cluster tolerance of something this payee charged on a day it
    booked exactly ONE row? That is the only evidence available for telling a split booking
    apart from two unrelated payments that happen to land together."""
    return any(a > 0 and abs(amount - a) / a <= AMOUNT_TOLERANCE for a in anchors)


def _merged_event(rows: list[dict]) -> dict:
    return {
        **rows[0],
        "amount": round(sum(r["amount"] for r in rows), 2),
        "ids": [r["id"] for r in rows],
    }


def _merge_one_day(group: list[dict], anchors: list[float]) -> list[dict]:
    """Merge one payee's rows for one day into as few events as the evidence supports.

    Whole-day sum first (the common split booking), then the largest subset that matches a
    known amount, and only failing both does it fall back to fusing everything — so the
    pre-2026-08-23 behaviour still stands wherever nothing distinguishes the rows.
    """
    if len(group) == 1:
        return [_merged_event(group)]
    total = sum(r["amount"] for r in group)
    if _matches_a_known_amount(total, anchors) or len(group) > _MAX_SAME_DAY_SUBSET:
        return [_merged_event(group)]
    for size in range(len(group) - 1, 0, -1):
        for subset in combinations(group, size):
            if not _matches_a_known_amount(sum(r["amount"] for r in subset), anchors):
                continue
            rest = [r for r in group if not any(r is s for s in subset)]
            return [_merged_event(list(subset))] + [_merged_event([r]) for r in rest]
    return [_merged_event(group)]


def _merge_same_day(rows: list[dict]) -> list[dict]:
    """Collapse same payee + same day into ONE event — but never fuse UNRELATED money.

    A single real payment is often recorded as several ledger rows. Example: an instalment on
    a 12-month instalment plan was booked as 2,100.00 + 118.40 — together exactly the 1,350.00
    charged every other month. Clustering per-row saw only the larger fragment, so the stream
    read as a payment that had shrunk, and the projection reported a total that didn't match a
    bill that is always 1,350.00 (a support report flagged the mismatch).

    Summing a day BLINDLY is the opposite failure, and it is worse. Example: a monthly 1,600.00
    instant-transfer loan repayment landed on 28 Jul beside an unrelated 900.00 transfer from
    the same payer. The day summed to 4,900.00, outside the +/-5% band, so July left the
    cluster, the stream's last occurrence became 27 Jun, `_is_stale` marked it ended, and a
    live monthly INFLOW stopped being projected at all — while the outflow it reimburses kept
    projecting, understating free cash flow by 3,200/mo.

    So a day is merged only as far as the payee's own history supports: days where exactly one
    row was booked are unambiguous observations, and a subset that matches one of those is a
    split booking. Anything else stays separate.
    """
    by_day: dict[tuple[str, date], list[dict]] = {}
    for row in rows:
        by_day.setdefault((row.get("payee_id") or "", row["date"]), []).append(row)
    anchors = [g[0]["amount"] for g in by_day.values() if len(g) == 1]
    merged: list[dict] = []
    for group in by_day.values():
        merged.extend(_merge_one_day(group, anchors))
    return sorted(merged, key=lambda r: r["date"])


def _classify_cadence(gaps: list[int]) -> str | None:
    """A cadence only if the MEDIAN gap lands in a known window AND every gap does too.

    The all-gaps check is what separates a real obligation from habitual spend: five coffees
    in a week have a small median gap but wildly irregular individual gaps, so they classify
    as nothing and fall through to the variable baseline where they belong.
    """
    if not gaps:
        return None
    ordered = sorted(gaps)
    median = ordered[len(ordered) // 2]
    for name, (lo, hi, _, _, _) in CADENCES.items():
        if lo <= median <= hi and all(lo <= g <= hi for g in gaps):
            return name
    return None


def _signature(direction: str, payee_id: str, cadence: str, amount: float) -> str:
    """Stable key for the user-overlay join. Rounded to the amount BAND (not the exact
    amount) so a $9.99→$10.99 rise keeps the user's dismiss/confirm attached."""
    band = round(amount / max(amount * AMOUNT_TOLERANCE, 0.01))
    raw = f"{direction}|{payee_id}|{cadence}|{band}"
    return "rs_" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def _stream_from_cluster(
    cluster: list[dict], direction: str, payee_id: str, today: date | None = None
) -> dict | None:
    if len(cluster) < TENTATIVE_OCCURRENCES:
        return None
    ordered = sorted(cluster, key=lambda r: r["date"])
    gaps = [(ordered[i + 1]["date"] - ordered[i]["date"]).days for i in range(len(ordered) - 1)]
    cadence = _classify_cadence(gaps)
    if not cadence:
        return None
    # The LATEST amount, not the mean. A projection is a claim about what happens NEXT, and
    # for anything whose amount moves — a loan instalment, a rent review, a subscription price
    # rise — the mean permanently lags reality: $9.99 rising to $10.99 would project $10.49
    # forever, and it never converges because every future month re-averages the old figures.
    # The mean is still what decides cluster MEMBERSHIP (a stable centre is the right thing to
    # test "is this the same stream?" against); it is only the wrong thing to forecast with.
    amount = round(ordered[-1]["amount"], 2)
    mean = round(sum(r["amount"] for r in ordered) / len(ordered), 2)
    last = ordered[-1]["date"]
    return {
        # Signature keys on the MEAN so a user's confirm/dismiss stays attached as the latest
        # amount drifts — keying on a moving value would silently detach the override.
        "key": _signature(direction, payee_id, cadence, mean),
        "direction": direction,
        "payee_id": payee_id,
        "label": ordered[-1]["label"],
        "cadence": cadence,
        "amount_base": amount,
        "average_amount_base": mean,
        "monthly_equivalent": round(amount * CADENCES[cadence][4], 2),
        "anchor_date": last.isoformat(),
        "last_date": last.isoformat(),
        "next_expected_date": _step(last, cadence).isoformat(),
        "occurrences": len(ordered),
        "confidence": "matured" if len(ordered) >= MATURED_OCCURRENCES else "tentative",
        "ended": _is_stale(last, cadence, today or user_today()),
        "source": "detected",
        "txn_ids": [tid for r in ordered for tid in r.get("ids") or [r["id"]]],
    }


def detect_streams(rows: list[dict], today: date | None = None) -> list[dict]:
    """Cluster pre-normalized rows into recurring streams.

    Each row: {id, date: date, amount: float (base, positive), payee_id, label, direction}.
    Grouping keys on ``payee_id`` — the ledger already resolves it at the write choke, so
    this never re-derives a fuzzy description key (``services.payees._normalize_payee``
    stays the single normalizer of record).
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if not row.get("payee_id"):
            continue  # no stable identity → can only ever be variable-baseline spend
        grouped.setdefault((row["direction"], row["payee_id"]), []).append(row)
    # One real payment is often several ledger rows — merge before clustering (see _merge_same_day).
    grouped = {k: _merge_same_day(v) for k, v in grouped.items()}

    streams: list[dict] = []
    for (direction, payee_id), group in grouped.items():
        found = [
            stream
            for cluster in _cluster_by_amount(group)
            if (stream := _stream_from_cluster(cluster, direction, payee_id, today))
        ]
        varying = _varying_stream(group, found, direction, payee_id, today)
        if varying:
            absorbed = set(varying["txn_ids"])
            found = [s for s in found if not set(s["txn_ids"]) & absorbed] + [varying]
        streams.extend(found)
    return sorted(streams, key=lambda s: -s["monthly_equivalent"])
    # A "coverage gate" was tried here and REVERTED — recorded so it is not re-attempted.
    # The idea: a real subscription accounts for most of its payee's traffic, while two
    # coincidentally-similar coffees sit in a crowd of unrelated visits, so require a cluster
    # to cover ≥60% of the payee's transactions. It does suppress the coffee pair — and it
    # ALSO killed the salary in the very book this work exists to fix: that employer pays a
    # monthly salary AND several one-off equity-sale proceeds, so the salary covered only
    # 3/8 of its payee's traffic and was rejected outright. Measured coverage does not
    # separate the two cases (salary 0.375 vs coffee 0.4 — the wrong way round), and a missed
    # salary is far worse than a surfaced-but-unprojected candidate.
    # The occurrence count already does this job honestly: a coincidental pair stays
    # `tentative` and is NEVER projected, and only the user confirming it promotes it.


# A bill whose amount moves every time (cloud hosting, a utility, a phone bill) is still a bill
# when it lands on a steady rhythm: the ±5% amount clusters split AWS's 254 / 262 / 227 / 197 /
# 192 / 94 into pairs, so it never reached three payments and read as day-to-day spending
# (owner, 2026-10-06: "a bill can't be 100% the same every month"). Only OUT money, and only
# monthly or longer — a weekly habit at one shop is groceries, not a bill.
_VARYING_CADENCES = ("monthly", "quarterly", "annual")


def _varying_stream(
    group: list[dict], found: list[dict], direction: str, payee_id: str, today: date | None
) -> dict | None:
    """One stream over the payee's rows the amount clusters did not mature: a chain with one
    payment per period on a monthly-or-longer rhythm, tolerating a RARE extra charge between
    (AWS bills a separate 6.19 eight days after one monthly bill)."""
    if direction != "out":
        return None
    claimed = {t for s in found if s["confidence"] == "matured" for t in s["txn_ids"]}
    rest = sorted(
        (r for r in group if not set(r.get("ids") or [r["id"]]) & claimed),
        key=lambda r: r["date"],
    )
    if len(rest) < MATURED_OCCURRENCES:
        return None
    for cadence in _VARYING_CADENCES:
        chain = _longest_chain(rest, cadence)
        if len(chain) >= MATURED_OCCURRENCES:
            stream = _stream_from_cluster(chain, direction, payee_id, today)
            if stream and stream["cadence"] == cadence:
                # Project the AVERAGE: the latest of an amount that swings is noise (AWS's
                # latest 94 against an average of 204), unlike a steady stream's price rise.
                mean = stream["average_amount_base"]
                return {
                    **stream,
                    "amount_base": mean,
                    "monthly_equivalent": round(mean * CADENCES[cadence][4], 2),
                    "amount_varies": True,
                }
    return None


def _longest_chain(rows: list[dict], cadence: str) -> list[dict]:
    """The longest run of rows each one cadence-gap after the last — the row nearest the
    expected date when several fit, as a bill lands on its day. Empty unless the run is most
    of the payee's traffic: the rows it leaves out may number at most half its length, so a
    merchant visited often (a travel site, a convenience store, rides) never reads as a bill
    because three of its visits happened to fall a month apart. (This coverage test is safe
    HERE, unlike the reverted gate in detect_streams: it only gates the varying-amount path,
    never a steady stream such as a salary beside one-off proceeds.)"""
    lo, hi = CADENCES[cadence][0], CADENCES[cadence][1]
    span = (rows[-1]["date"] - rows[0]["date"]).days
    if len(rows) > (span // lo + 1) * 1.5:  # too much traffic for any chain to cover — bail
        return []
    nominal = (lo + hi) / 2
    best: list[dict] = []
    for start in range(len(rows)):
        chain, i = [rows[start]], start
        while True:
            fits = []
            for j in range(i + 1, len(rows)):
                gap = (rows[j]["date"] - rows[i]["date"]).days
                if gap > hi:
                    break
                if gap >= lo:
                    fits.append(j)
            if not fits:
                break
            i = min(fits, key=lambda j: abs((rows[j]["date"] - rows[i]["date"]).days - nominal))
            chain.append(rows[i])
        if len(chain) > len(best):
            best = chain
    if (len(rows) - len(best)) * 2 > len(best) or not _holds_its_day(best):
        return []
    return best


def _holds_its_day(chain: list[dict]) -> bool:
    """A short chain must land on the same day of the month (±3) — a bill does; three
    purchases that happened to fall about a month apart don't. Four or more regular payments
    are evidence enough on their own."""
    if len(chain) > MATURED_OCCURRENCES:
        return True
    days = [r["date"].day for r in chain]
    return max(days) - min(days) <= 3


def _is_stale(last: date, cadence: str, today: date) -> bool:
    """Has this stream stopped happening?

    Without this a stream projects forever on the strength of old history. A pay RISE is the
    case that exposes it: 9,000 → 9,750 is a 9% jump, outside the ±5% amount tolerance, so
    the new figure starts a SEPARATE cluster that needs 3 occurrences to mature — while the
    old 8-occurrence stream stays "matured" and keeps projecting the superseded salary. The
    forecast would show the old number for months, and (because the new transactions are not
    claimed by an active stream) also average the raise into the variable bucket — counting
    both. The same shape covers a cancelled subscription or a job change.

    Missing 1.5 of its own cadence is the test: one late payment is normal, two consecutive
    misses is a stream that ended. Scaled to the cadence, so a monthly stream is written off
    after ~7 weeks while an annual one gets ~1.5 years.
    """
    window_hi = CADENCES[cadence][1]
    return (today - last).days > window_hi * STALE_AFTER_PERIODS


def _step(from_date: date, cadence: str, steps: int = 1) -> date:
    _, _, months_step, days_step, _ = CADENCES[cadence]
    if months_step:
        y, m = _add_months(from_date.year, from_date.month, months_step * steps)
        return _clamp_day(y, m, from_date.day)
    return from_date + timedelta(days=days_step * steps)


# ── projection ────────────────────────────────────────────────────────────────────


def occurrences_between(stream: dict, after: date, through: date) -> list[date]:
    """The dates this stream lands on in the window (``after``, ``through``] — the day-level
    twin of ``stream_month_amounts``, walking the same anchor/cadence/active-range rules.
    ``after`` is exclusive for the same reason ``today`` is there: a payment dated on or before
    it has already posted and is already in the ledger."""
    anchor = _parse(stream.get("anchor_date")) or _parse(stream.get("last_date"))
    cadence = stream.get("cadence")
    if not anchor or cadence not in CADENCES:
        return []
    out: list[date] = []
    cursor, guard = anchor, 0
    while cursor <= after and guard < 5000:
        cursor, guard = _step(cursor, cadence), guard + 1
    while cursor <= through and guard < 5000:
        out.append(cursor)
        cursor, guard = _step(cursor, cadence), guard + 1
    return out


def stream_month_amounts(
    stream: dict, from_ym: str, months: int, today: date | None = None
) -> dict[str, float]:
    """{year_month: total_base} this stream contributes over the horizon.

    Walks REAL occurrence dates and buckets them, so a weekly stream correctly lands 4 or 5
    times in a month and an annual one lands in exactly one — the "don't smooth lumpy items"
    rule from direct-method cash-flow practice.

    ``today``, when given, drops any occurrence dated on or before it: that payment has
    already posted and is already counted in the caller's real-transaction actuals, so
    projecting it again here would double it. It never affects a later month — only the
    in-progress one can contain a date `<= today`.
    """
    anchor = _parse(stream.get("anchor_date")) or _parse(stream.get("last_date"))
    cadence = stream.get("cadence")
    if not anchor or cadence not in CADENCES:
        return {}
    start_y, start_m = int(from_ym[:4]), int(from_ym[5:7])
    end_y, end_m = _add_months(start_y, start_m, months)
    horizon_end = _clamp_day(end_y, end_m, 1)
    window_start = _clamp_day(start_y, start_m, 1)

    out: dict[str, float] = {}
    cursor, guard = anchor, 0
    while cursor < window_start and guard < 5000:  # fast-forward to the window
        cursor, guard = _step(cursor, cadence), guard + 1
    while cursor < horizon_end and guard < 5000:
        if not (today and cursor <= today):
            ym = f"{cursor.year:04d}-{cursor.month:02d}"
            out[ym] = round(out.get(ym, 0.0) + float(stream.get("amount_base") or 0), 2)
        cursor, guard = _step(cursor, cadence), guard + 1
    return out
