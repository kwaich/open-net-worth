"""dividend_calendar — upcoming dividend payments per holding (ADR 0020).

A read-only projection over CURRENT holdings (`positions_service.hydrated()`) + the cached
instrument dividend metadata that `prices.enrich_dividend_meta` refreshes daily. This NEVER hits
the network — it reads cached fields and projects in-process.

Two-tier dating (the honesty contract):
  - SCHEDULED — the instrument carries a Yahoo-DECLARED ``next_ex_date`` still in the future:
    use it (+ ``next_pay_date``) verbatim. These are the green "announced" rows.
  - ESTIMATED — otherwise project the next ex-date as ``last_ex_date + cadence`` rolled past
    today, pay-date = ex + ``PAY_OFFSET_DAYS`` (Yahoo history exposes ex-dates only). Grey rows.
  - Bond coupons: always ``status="scheduled"`` (issuer-declared schedule counts back from
    maturity). Principal-at-maturity rows flag ``kind="principal"`` when maturity ∈ horizon.

Non-payers (no declared rate AND no history → no per-share amount, or undatable) are EXCLUDED,
never shown with a fabricated date or amount. Amounts are estimates; the UI labels them as such.
All base conversion goes through ``core.fx.to_base`` (the income/lots "recompute on read"
invariant). Imports are downward/sibling only (core + db + services) — no api/ai (Contract 3).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from backend.core.fx import base_currency, to_base
from backend.core.user_time import user_today
from backend.db.instrument_store import InstrumentStore
from backend.services import fixed_income
from backend.services import lots as lots_service
from backend.services import positions as positions_service

# Convenience handle so tests monkeypatch one module surface (mirrors income_service).
instruments = InstrumentStore()

_INTERVAL_DAYS = {"monthly": 30, "quarterly": 91, "semi-annual": 182, "annual": 365}
_FREQ_PER_YEAR = {"monthly": 12, "quarterly": 4, "semi-annual": 2, "annual": 1}
# Estimated pay-date offset from ex-date. The real offset varies (AAPL +3d, MSFT +21d) and
# Yahoo history gives ex-dates only, so a constant is honest given the grey "estimated" label.
PAY_OFFSET_DAYS = 14
DEFAULT_HORIZON_DAYS = 90
MAX_HORIZON_DAYS = 365


def _per_share(inst: dict, freq: Optional[str]) -> Optional[float]:
    """Best per-payment per-share amount: the last actual dividend, else the declared annual
    rate divided by the payment frequency. None ⇒ we can't price the payment (excluded)."""
    amount = inst.get("last_dividend_amount")
    if amount:
        return float(amount)
    rate = inst.get("dividend_rate_annual")
    per_year = _FREQ_PER_YEAR.get(freq or "")
    if rate and per_year:
        return float(rate) / per_year
    return None


def _next_event(inst: dict, today: date) -> Optional[tuple[str, str, str]]:
    """(ex_date, pay_date, status) for the next payment, or None if undatable.

    SCHEDULED when a declared future ``next_ex_date`` exists (a stale past one is ignored —
    that's the self-healing read-time guard); otherwise project the next ex-date from the last
    historical ex-date + cadence."""
    next_ex = inst.get("next_ex_date")
    if next_ex and next_ex >= today.isoformat():
        pay = inst.get("next_pay_date")
        if not pay or pay < next_ex:
            pay = (date.fromisoformat(next_ex) + timedelta(days=PAY_OFFSET_DAYS)).isoformat()
        return next_ex, pay, "scheduled"

    last_ex = inst.get("last_ex_date")
    interval = _INTERVAL_DAYS.get(inst.get("dividend_frequency") or "")
    if last_ex and interval:
        try:
            ex = date.fromisoformat(last_ex)
        except ValueError:
            return None
        while ex < today:  # roll forward to the first ex-date ON OR AFTER today
            ex = ex + timedelta(days=interval)
        pay = ex + timedelta(days=PAY_OFFSET_DAYS)
        return ex.isoformat(), pay.isoformat(), "estimated"
    return None


def next_ex_date(inst: dict, today: date) -> Optional[str]:
    """The instrument's next ex-dividend date (declared, else projected from its cadence)."""
    event = _next_event(inst, today)
    return event[0] if event else None


def _row_base(kind: str, principal_at_maturity: bool) -> dict:
    """Shape fields common to every payment dict regardless of type."""
    return {"kind": kind, "principal_at_maturity": principal_at_maturity}


def _coupon_payments(
    inst: dict,
    h: dict,
    today: date,
    horizon: date,
    base: str,
    rates: dict,
) -> list[dict]:
    """Build coupon + principal rows for a bond holding.

    Coupon bonds: emit one row per date from ``fixed_income.coupon_schedule``, tagged
    ``kind="coupon"``.  When maturity falls within the horizon, also emit a separate
    ``kind="principal"`` row and flag the final coupon ``principal_at_maturity=True``.
    Zero-coupon / T-bill: no coupon rows; one ``kind="principal"`` row at maturity if
    it falls within the horizon.
    """
    bt = fixed_income.terms(inst)
    qty = float(h.get("quantity") or 0)
    quote_ccy = (h.get("quote_currency") or inst.get("quote_currency") or base).upper()
    face = float((bt or {}).get("face_value") or 100.0)
    maturity = fixed_income._parse_date((bt or {}).get("maturity_date"))
    freq = int((bt or {}).get("coupon_freq_months") or 0)

    rows: list[dict] = []
    annual_coupon = fixed_income.annual_coupon_per_unit(inst)

    if annual_coupon > 0 and freq > 0:
        freq_per_year = 12.0 / freq
        per_payment = annual_coupon / freq_per_year
        coupon_dates = fixed_income.coupon_schedule(inst, today, horizon)
        for i, cd in enumerate(coupon_dates):
            cd_iso = cd.isoformat()
            is_last = i == len(coupon_dates) - 1
            flag_principal = is_last and bool(maturity and today <= maturity <= horizon)
            amt = to_base(per_payment * qty, quote_ccy, base, rates=rates)
            rows.append(
                {
                    "position_id": h.get("position_id"),
                    "instrument_id": inst["id"],
                    "symbol": h.get("symbol") or inst.get("symbol") or "",
                    "name": inst.get("name") or h.get("label") or "",
                    "ex_date": cd_iso,
                    "pay_date": cd_iso,
                    "per_share": round(per_payment, 6),
                    "currency": quote_ccy,
                    "amount_base": round(amt, 2),
                    "status": "scheduled",
                    **_row_base("coupon", flag_principal),
                }
            )

    if maturity and today <= maturity <= horizon:
        proceeds = to_base(face * qty, quote_ccy, base, rates=rates)
        rows.append(
            {
                "position_id": h.get("position_id"),
                "instrument_id": inst["id"],
                "symbol": h.get("symbol") or inst.get("symbol") or "",
                "name": inst.get("name") or h.get("label") or "",
                "ex_date": maturity.isoformat(),
                "pay_date": maturity.isoformat(),
                "per_share": round(face, 6),
                "currency": quote_ccy,
                "amount_base": round(proceeds, 2),
                "status": "scheduled",
                **_row_base("principal", False),
            }
        )

    return rows


def upcoming_payments(days: int = DEFAULT_HORIZON_DAYS, base: Optional[str] = None) -> dict:
    """Per-holding upcoming dividend payments within ``days`` (clamped 1–365; ``0`` ⇒ default 90),
    sorted by ex-date.  Bond coupons and principal-at-maturity appear as distinct rows
    (``kind="coupon"``/``"principal"``); dividends carry ``kind="dividend"``.

    Empty portfolio / no payers ⇒ ``{count: 0, total_base: 0.0, next_payment: None}`` (no crash,
    no div-by-zero)."""
    base = (base or base_currency()).upper()
    days = max(1, min(int(days or DEFAULT_HORIZON_DAYS), MAX_HORIZON_DAYS))
    rates = lots_service._load_rates()
    today = user_today()
    today_iso = today.isoformat()
    horizon = today + timedelta(days=days)
    horizon_iso = horizon.isoformat()

    by_id = {i["id"]: i for i in instruments.all()}
    payments: list[dict] = []
    for h in positions_service.hydrated():
        qty = float(h.get("quantity") or 0)
        if qty <= 0:
            continue
        # Unvested equity-grant units are not owned yet (ADR
        # 2026-07-31-unvested-equity-excluded-from-net-worth) — never a real upcoming
        # payment, coupon included.
        if h.get("vesting_state") == "unvested":
            continue
        inst = by_id.get(h.get("instrument_id"))
        if not inst:
            continue

        if fixed_income.is_fixed_income(inst):
            rows = _coupon_payments(inst, h, today, horizon, base, rates)
            payments.extend(rows)
            continue

        per_share = _per_share(inst, inst.get("dividend_frequency"))
        event = _next_event(inst, today)
        if per_share is None or event is None:
            continue  # non-payer or undatable → excluded (never fabricate)
        ex_date, pay_date, status = event
        if not (today_iso <= ex_date <= horizon_iso):
            continue
        interval = _INTERVAL_DAYS.get(inst.get("dividend_frequency") or "")
        quote_ccy = (h.get("quote_currency") or inst.get("quote_currency") or base).upper()
        qpu = float(h.get("quote_per_unit") or inst.get("quote_per_unit") or 1.0)
        amount_base = to_base(per_share * qty * qpu, quote_ccy, base, rates=rates)
        # Only the FIRST occurrence can be the issuer-declared next_ex_date/next_pay_date
        # ("scheduled"); roll the remaining occurrences forward by cadence through the
        # horizon so a quarterly/monthly payer forecasts its full schedule, not one row
        # (MATH-36). Each rolled occurrence is a projection, so it's "estimated".
        while ex_date <= horizon_iso:
            payments.append(
                {
                    "position_id": h.get("position_id"),
                    "instrument_id": inst["id"],
                    "symbol": h.get("symbol") or inst.get("symbol") or "",
                    "name": inst.get("name") or h.get("label") or "",
                    "ex_date": ex_date,
                    "pay_date": pay_date,
                    "per_share": round(per_share, 6),
                    "currency": quote_ccy,
                    "amount_base": round(amount_base, 2),
                    "status": status,
                    **_row_base("dividend", False),
                }
            )
            if not interval:
                break
            next_ex = date.fromisoformat(ex_date) + timedelta(days=interval)
            ex_date = next_ex.isoformat()
            pay_date = (next_ex + timedelta(days=PAY_OFFSET_DAYS)).isoformat()
            status = "estimated"

    payments.sort(key=lambda p: (p["ex_date"], p["pay_date"], p["symbol"]))
    # Principal redemption is return-of-capital, not income — excluded from income total.
    income_total = round(sum(p["amount_base"] for p in payments if p.get("kind") != "principal"), 2)
    principal_total = round(
        sum(p["amount_base"] for p in payments if p.get("kind") == "principal"), 2
    )
    return {
        "base_currency": base,
        "horizon_days": days,
        "payments": payments,
        "count": len(payments),
        "total_base": income_total,
        "principal_base": principal_total,
        "next_payment": payments[0] if payments else None,
    }
