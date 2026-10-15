"""risk_checks — the risk engine's building blocks and its two book-wide checks (unhedged
foreign-currency exposure, drawdown from cost), over the invested book ``risk_signals`` projects.
Pure: no I/O."""

from __future__ import annotations


def finding(
    kind: str,
    label: str,
    ticker: str,
    severity: str,
    pct: float | None,
    thr: float,
    reason: str,
    impact: str,
    action: str,
    iids: list[str],
) -> dict:
    return {
        "kind": kind,
        "label": label,
        "ticker": ticker,
        "severity": severity,
        "metric_pct": pct,
        "threshold_pct": thr,
        "status": "action_needed",
        "status_reason": reason,
        "portfolio_impact": impact,
        "action_items": [action],
        "affected_instrument_ids": iids,
    }


def share_pct(value: float, total: float) -> float:
    return round(value / total * 100, 2) if total else 0.0


def grouped(projected: list[dict], key) -> dict[str, tuple[float, list[str]]]:
    """key(row) → (summed base value, instrument ids). A blank key is skipped."""
    groups: dict[str, tuple[float, list[str]]] = {}
    for p in projected:
        k = key(p)
        if not k:
            continue
        value, iids = groups.get(k, (0.0, []))
        iid = p.get("instrument_id") or ""
        groups[k] = (value + p["base_value"], iids if iid in iids else [*iids, iid])
    return groups


def fx_findings(projected: list[dict], grand: float, thresholds: dict, base: str) -> list[dict]:
    def foreign(p: dict) -> str:
        ccy = (p.get("quote_currency") or p.get("currency") or base).upper()
        return "" if ccy == base else ccy

    groups = grouped(projected, foreign)
    fx_bv = sum(v for v, _ in groups.values())
    fx_iids = [i for _, ids in groups.values() for i in ids]
    thr = thresholds["fx_pct"]
    fx_pct = share_pct(fx_bv, grand)
    if fx_pct <= thr:
        return []
    reason = f"Unhedged foreign-currency holdings are {fx_pct:.1f}% of your investments."
    return [
        finding(
            "fx_mismatch",
            "FX Mismatch",
            "",
            "medium",
            fx_pct,
            thr,
            reason,
            f"A 10% adverse FX move could reduce your investments by approx {base} {round(fx_bv * 0.1):,}.",
            "Review unhedged currency exposure and consider FX hedging or rebalancing.",
            fx_iids,
        )
    ]


def unvalued_fx_finding(unvalued: dict[str, list[str]], thresholds: dict, base: str) -> dict:
    """The honest stand-in for holdings the book could not value: no exchange rate to `base`
    means no share can be computed for them, and saying nothing would read as "no risk"."""
    ccys = ", ".join(sorted(unvalued))
    return finding(
        "fx_mismatch",
        "FX Mismatch",
        "",
        "medium",
        None,
        thresholds["fx_pct"],
        f"No exchange rate to {base} is available for {ccys} (missing), so those holdings "
        "could not be valued and the concentration checks are paused.",
        "Concentration, sector, region and foreign-currency shares need the whole book valued; "
        "they are not shown until the missing rate is available.",
        "Refresh exchange rates; the risk checks resume once a rate is available.",
        [i for ids in unvalued.values() for i in ids],
    )


def drawdown_findings(projected: list[dict], thresholds: dict, base: str) -> list[dict]:
    """Per INSTRUMENT (legs at several accounts summed), from the same base cost the Investments
    P&L column uses. A leg with an unknown cost makes the whole holding unknown — never a loss
    invented from the known legs alone."""
    thr = thresholds["drawdown_pct"]
    by_iid: dict[str, list[dict]] = {}
    for p in projected:
        by_iid.setdefault(p.get("instrument_id") or "", []).append(p)
    out = []
    for iid, legs in by_iid.items():
        if any(not leg.get("cost_base") for leg in legs):
            continue
        mv = sum(leg["base_value"] for leg in legs)
        cost = sum(leg["cost_base"] for leg in legs)
        drawdown = round((mv / cost - 1) * 100, 2)
        if drawdown >= -thr:
            continue
        label = legs[0].get("label") or legs[0].get("ticker") or iid
        out.append(
            finding(
                "drawdown",
                label,
                legs[0].get("ticker") or "",
                "high" if drawdown < -(thr * 1.5) else "medium",
                abs(drawdown),
                thr,
                f"{label} is down {abs(drawdown):.1f}% from cost basis, exceeding the {thr:.0f}% drawdown threshold.",
                f"Unrealized loss of approx {base} {round(cost - mv):,}.",
                f"Review {label} — consider whether the thesis still holds or a stop-loss is warranted.",
                [iid],
            )
        )
    return out
