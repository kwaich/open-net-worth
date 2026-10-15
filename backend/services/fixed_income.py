"""fixed_income — first-class bond valuation, coupon schedules, and the fixed-income lens.

The ONE place that derives a fixed-income mark and coupon math from an instrument's
``bond_terms`` (see ``db/instrument_store._coerce_bond_terms`` and the bonds-first-class
RFC). Pure, import-light: stdlib + ``db`` reads + sibling services only (no api/ai), so it
sits cleanly in the ``services`` layer.

Valuation reuses the existing ``value = qty × price × quote_per_unit`` identity — this
module only supplies the per-unit ``price`` as a **percent of par** for the subtypes whose
mark is computed rather than quoted:

    ssb   → 100                      (always redeemable at par)
    tbill → issue_price → 100        (linear accretion over issue → maturity)
    fd    → 100 + accrued interest   (principal grows by simple interest to date)
    market / etf / non-bond → None   (fall through to the market/manual price)

Coupon income is a FRACTION rate × face × qty, surfaced to the income forecast and a
deterministic coupon reminder. Actual cash is booked through the existing income choke
point on confirmation — this module never moves money.
"""

from __future__ import annotations

from datetime import date, datetime

from backend.core.user_time import user_today

# Subtypes whose mark this module computes (vs. market/etf which are quoted).
_COMPUTED_MARK_SUBTYPES = {"ssb", "tbill", "fd"}
# Subtypes that pay a periodic coupon off coupon_rate_annual (tbill is zero-coupon: its
# return is the discount accretion, surfaced separately; etf distributions ride the
# normal dividend path).
_COUPON_SUBTYPES = {"market", "ssb", "fd"}

_DAYS_PER_YEAR = 365.0
# Hard cap on the coupon date-walk — ~170y at monthly frequency, far beyond any real bond.
# A backstop against a malformed maturity/frequency producing an unbounded or crashing walk.
_MAX_COUPON_ITERS = 2048


# ── Term access ────────────────────────────────────────────────────────────────────


def terms(inst: dict | None) -> dict | None:
    """The instrument's bond_terms iff it is a bond with terms, else None."""
    if not inst or inst.get("asset_class") != "bond":
        return None
    bt = inst.get("bond_terms")
    return bt if isinstance(bt, dict) else None


def is_fixed_income(inst: dict | None) -> bool:
    """True for any bond instrument (the asset class), terms or not."""
    return bool(inst) and inst.get("asset_class") == "bond"


def subtype(inst: dict | None) -> str:
    t = terms(inst)
    return (t.get("subtype") or "") if t else ""


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


# ── Computed mark (percent of par) ──────────────────────────────────────────────────


def computed_mark(inst: dict | None, as_of: date | None = None) -> float | None:
    """The per-unit price as a PERCENT OF PAR for a computed-mark subtype, else None.

    None means "this module has no opinion — use the market/manual price" (market bonds,
    bond ETFs, and any non-bond). The returned number plugs straight into the existing
    ``value = qty × price × quote_per_unit`` fold (with quote_per_unit = face/100)."""
    t = terms(inst)
    if not t:
        return None
    st = t.get("subtype") or ""
    if st not in _COMPUTED_MARK_SUBTYPES:
        return None
    as_of = as_of or user_today()
    if st == "ssb":
        return 100.0
    issue = _parse_date(t.get("issue_date"))
    maturity = _parse_date(t.get("maturity_date"))
    if st == "tbill":
        issue_price = _as_float(t.get("issue_price"), 100.0)
        if not (issue and maturity) or maturity <= issue:
            return issue_price  # not enough info to accrete — hold at entry
        frac = _clamp((as_of - issue).days / (maturity - issue).days, 0.0, 1.0)
        return round(issue_price + (100.0 - issue_price) * frac, 6)
    # fd: principal accrues simple interest to date (capped at maturity).
    rate = _as_float(t.get("coupon_rate_annual"), 0.0)
    if not issue:
        return 100.0
    end = min(as_of, maturity) if maturity else as_of
    days = max(0, (end - issue).days)
    return round(100.0 * (1.0 + rate * days / _DAYS_PER_YEAR), 6)


def _as_float(value, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


# ── Coupon math ─────────────────────────────────────────────────────────────────────


def annual_coupon_per_unit(inst: dict | None) -> float:
    """Annual coupon in quote currency per HOLDING UNIT (face × rate). 0 if not coupon-paying."""
    t = terms(inst)
    if not t or (t.get("subtype") or "") not in _COUPON_SUBTYPES:
        return 0.0
    rate = _as_float(t.get("coupon_rate_annual"), 0.0)
    face = _as_float(t.get("face_value"), 100.0)
    return round(face * rate, 6)


def annual_income_per_unit(inst: dict | None) -> tuple[float, str]:
    """Forward annual income per holding unit + its source label, for the income forecast.

    coupon bonds → (face × rate, "coupon"); a T-bill → (annualized discount accretion,
    "accretion"); anything else → (0, "none")."""
    t = terms(inst)
    if not t:
        return 0.0, "none"
    st = t.get("subtype") or ""
    if st in _COUPON_SUBTYPES:
        coupon = annual_coupon_per_unit(inst)
        return (coupon, "coupon") if coupon else (0.0, "none")
    if st == "tbill":
        issue = _parse_date(t.get("issue_date"))
        maturity = _parse_date(t.get("maturity_date"))
        face = _as_float(t.get("face_value"), 1.0)
        issue_price = _as_float(t.get("issue_price"), 100.0)
        discount = face * (100.0 - issue_price) / 100.0
        if issue and maturity and maturity > issue:
            years = max((maturity - issue).days / _DAYS_PER_YEAR, 1e-9)
            return round(discount / years, 6), "accretion"
        return round(discount, 6), "accretion"
    return 0.0, "none"


def coupon_schedule(inst: dict | None, start: date, end: date) -> list[date]:
    """Coupon payment dates in [start, end] derived from maturity_date + coupon_freq_months,
    counting backwards from maturity (the conventional anchor). [] if not a coupon bond or
    no frequency/maturity is set."""
    t = terms(inst)
    if not t or (t.get("subtype") or "") not in _COUPON_SUBTYPES:
        return []
    freq = t.get("coupon_freq_months")
    maturity = _parse_date(t.get("maturity_date"))
    if not freq or freq <= 0 or not maturity:
        return []
    dates: list[date] = []
    d = maturity
    iters = 0
    # Walk back from maturity (the conventional anchor), collecting dates in [start, end].
    # Bounded two ways so one malformed bond can never hang or crash the lens: an iteration
    # cap (_MAX_COUPON_ITERS), and a try/except around the month step in case an absurd
    # maturity/frequency pushes the date past datetime's year bounds (freq is already clamped
    # to ≤120 at the store, this is defence-in-depth).
    while d >= start and iters < _MAX_COUPON_ITERS:
        if d <= end:
            dates.append(d)
        try:
            d = _add_months(d, -int(freq))
        except (ValueError, OverflowError):
            break
        iters += 1
    return sorted(dates)


def next_coupon(inst: dict | None, as_of: date | None = None) -> dict | None:
    """The next coupon on/after as_of: {date, amount_per_unit} or None."""
    as_of = as_of or user_today()
    maturity = (terms(inst) or {}).get("maturity_date")
    horizon = _parse_date(maturity) or _add_months(as_of, 24)
    sched = coupon_schedule(inst, as_of, horizon)
    nxt = next((d for d in sched if d >= as_of), None)
    if not nxt:
        return None
    freq = int((terms(inst) or {}).get("coupon_freq_months") or 12)
    per_unit = round(annual_coupon_per_unit(inst) * (freq / 12.0), 6)
    return {"date": nxt.isoformat(), "amount_per_unit": per_unit}


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    year = d.year + m // 12
    month = m % 12 + 1
    # clamp day to month length (e.g. maturity on the 31st stepping into Feb)
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year + (month // 12), (month % 12) + 1, 1) - date(year, month, 1)).days


def days_to_maturity(inst: dict | None, as_of: date | None = None) -> int | None:
    maturity = _parse_date((terms(inst) or {}).get("maturity_date"))
    if not maturity:
        return None
    return (maturity - (as_of or user_today())).days


# ── Fixed-income lens (summary for the UI / get_fixed_income tool) ──────────────────


def summary(as_of: date | None = None, base: str | None = None) -> dict:
    """The fixed-income view: every bond holding valued in base currency, plus a by-subtype
    breakdown, cumulative maturity buckets (≤30d/≤90d/≤1y), and an upcoming maturity ladder.

    Reuses ``positions.hydrated`` for resolved marks (so market bonds pick up their manual
    price and computed-mark subtypes accrete) and the InstrumentStore for coupon schedules.
    Lazy imports break the positions ⇄ fixed_income module cycle. Excludes
    vesting_state="unvested" holdings (unvested equity-grant units — not owned yet),
    mirroring the net-worth fold."""
    from backend.core.fx import base_currency, to_base
    from backend.db.instrument_store import InstrumentStore
    from backend.services import lots as lots_service
    from backend.services import positions as positions_service

    as_of = as_of or user_today()
    base = (base or base_currency()).upper()
    rates = lots_service._load_rates()
    insts = {i["id"]: i for i in InstrumentStore().all()}

    holdings: list[dict] = []
    by_subtype: dict[str, float] = {}
    buckets = {"d30": 0.0, "d90": 0.0, "y1": 0.0}
    ladder: list[dict] = []
    total = 0.0
    ladder_excluded_count = 0

    for r in positions_service.hydrated():
        if r.get("asset_class") != "bond":
            continue
        # vesting_state="unvested" = unvested equity-grant units, forfeited if the grantee
        # leaves — not owned yet (ADR 2026-07-31-unvested-equity-excluded-from-net-worth).
        # No current flow mints an unvested bond, but add_positions accepts vesting_state
        # freely, so this is enforced at the read path rather than assumed impossible.
        if r.get("vesting_state") == "unvested":
            continue
        qty = float(r.get("quantity") or 0)
        qpu = float(r.get("quote_per_unit") or 1)
        value_base = round(
            to_base(
                float(r.get("price") or 0) * qty * qpu,
                r.get("quote_currency") or base,
                base,
                rates=rates,
            ),
            2,
        )
        total += value_base
        st = r.get("bond_type") or ""
        by_subtype[st] = round(by_subtype.get(st, 0.0) + value_base, 2)
        inst = insts.get(r.get("instrument_id"))
        # Isolate a malformed bond: a bad coupon schedule degrades to next_coupon=None for
        # that row only, never failing the whole fixed-income response.
        try:
            nxt = next_coupon(inst, as_of)
        except (ValueError, OverflowError):
            nxt = None
        dtm = r.get("days_to_maturity")
        holdings.append(
            {
                "instrument_id": r.get("instrument_id"),
                "symbol": r.get("symbol"),
                "name": r.get("label"),
                "account_name": r.get("account_name"),
                "subtype": st,
                "coupon_rate": r.get("coupon_rate"),
                "maturity_date": r.get("maturity_date"),
                "days_to_maturity": dtm,
                "value_base": value_base,
                "next_coupon": nxt,
            }
        )
        if dtm is None:
            ladder_excluded_count += 1
        if dtm is not None and dtm >= 0:
            # Redemption proceeds estimate. Use the current computed mark (r["price"]) rather
            # than hardcoded par: SSB/T-bill redeem at ~100, but an FD returns principal PLUS
            # accrued interest (its mark already carries the accrual), so par-100 would
            # understate an FD's payout. Market bonds redeem near par; their mark ≈ par anyway.
            mark = float(r.get("price") or 100.0)
            proceeds = round(
                to_base(mark * qty * qpu, r.get("quote_currency") or base, base, rates=rates), 2
            )
            if dtm <= 30:
                buckets["d30"] += proceeds
            if dtm <= 90:
                buckets["d90"] += proceeds
            if dtm <= 365:
                buckets["y1"] += proceeds
                ladder.append(
                    {
                        "instrument_id": r.get("instrument_id"),
                        "name": r.get("label"),
                        "subtype": st,
                        "maturity_date": r.get("maturity_date"),
                        "proceeds_base": proceeds,
                    }
                )

    ladder.sort(key=lambda x: x.get("maturity_date") or "")
    return {
        "base_currency": base,
        "total_base": round(total, 2),
        "holdings": holdings,
        "by_subtype": by_subtype,
        "maturity_buckets": {k: round(v, 2) for k, v in buckets.items()},
        "maturity_ladder": ladder,
        "count": len(holdings),
        "ladder_excluded_count": ladder_excluded_count,
    }
