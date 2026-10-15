"""watch_trend — the pulse row's trend line: the last 1M / 3M / 1Y of closes, with the benchmark on
the same dates. Pure: no I/O, no clock.

Inputs are the same dated closes ``watch_stats`` reads (``[(iso_date, close), …]``, excluding
today), plus today's live price. 1M is ``watch_stats.MOMENTUM_WINDOW`` closes back, so the caption
("−18% · −14% vs SPY") and the "month vs benchmark" flag are the same arithmetic and can't
disagree. 1Y is thinned to weekly points to keep the payload small; the last point is always
today's price.
"""

from __future__ import annotations

from backend.services.watch_stats import MOMENTUM_WINDOW, bench_on, bench_today, closes_of

PERIODS: dict[str, int] = {"1M": MOMENTUM_WINDOW, "3M": 63, "1Y": 252}
WEEKLY_FROM = 100  # periods longer than this many closes are thinned to every 5th close
MIN_SHARE = 0.6  # a period needs at least this share of its closes, else it's not drawn


def _thin(points: list[float], window: int) -> list[float]:
    if window <= WEEKLY_FROM or len(points) <= 2:
        return points
    head, last = points[:-1], points[-1]
    # Step back from the end so the spacing lands on today, then keep the very first close.
    kept = head[::-1][::5][::-1]
    if (len(head) - 1) % 5:
        kept = [head[0], *kept]
    return [*kept, last]


def _pct(first: float, last: float) -> float:
    return round((last / first - 1) * 100, 2)


def _period(
    closes: list[tuple[str, float]],
    window: int,
    price: float,
    bench: list[tuple[str, float]],
    bench_day: float | None,
    bench_price: float | None,
) -> dict | None:
    if len(closes) < max(2, int(window * MIN_SHARE)):
        return None
    span = closes[-window:]
    stock = [c for _, c in span] + [price]
    out: dict = {
        "closes": [round(c, 4) for c in _thin(stock, window)],
        "pct": _pct(stock[0], price),
    }
    out["bench"], out["vs_bench_pct"] = None, None
    on = bench_on([d for d, _ in span], bench) if bench and bench_day is not None else None
    today = bench_today(bench, bench_day, bench_price) if on else None
    if on and today:
        line = [*on, today]
        out["bench"] = [round(c, 4) for c in _thin(line, window)]
        out["vs_bench_pct"] = round(out["pct"] - _pct(line[0], today), 2)
    return out


def trend(
    series: list[tuple[str, float]],
    *,
    price: float | None,
    bench_series: list[tuple[str, float]] | None = None,
    bench_day: float | None = None,
    bench_price: float | None = None,
) -> dict | None:
    """``{period: {closes, pct, bench, vs_bench_pct} | None}``, or None when no period can be drawn.
    ``bench`` is the benchmark's closes on the stock's dates (None without a benchmark move)."""
    if not price or price <= 0:
        return None
    closes = closes_of(series)
    bench = closes_of(bench_series or [])
    out = {
        p: _period(closes, w, float(price), bench, bench_day, bench_price)
        for p, w in PERIODS.items()
    }
    return out if any(out.values()) else None


def row_trend(
    series: list[tuple[str, float]],
    *,
    price: float | None,
    bench: dict | None,
    bench_series: list[tuple[str, float]] | None,
    sector: dict | None = None,
    sector_series: list[tuple[str, float]] | None = None,
) -> tuple[dict | None, dict | None]:
    """(trend, compare) for one pulse row. The dashed line is the SECTOR fund when ``sector``
    (``{symbol, day_pct}``) is given and its trend draws — a watchlist tab's "is it winning in its
    industry" — else the benchmark ("what the index would have done"). With a sector line each
    period also carries ``vs_market_pct`` (the benchmark gap), so the tooltip can give both."""
    bench = bench or {}
    base = trend(
        series,
        price=price,
        bench_series=bench_series,
        bench_day=bench.get("day_pct"),
        bench_price=bench.get("price"),
    )
    if base is None:
        return None, None
    if sector and sector_series:
        vs_sector = trend(
            series,
            price=price,
            bench_series=sector_series,
            bench_day=sector.get("day_pct"),
            bench_price=sector.get("price"),
        )
        if vs_sector and any(p and p["bench"] for p in vs_sector.values()):
            for period, p in vs_sector.items():
                if p is not None:
                    b = base.get(period)
                    p["vs_market_pct"] = b["vs_bench_pct"] if b else None
            return vs_sector, {"symbol": sector["symbol"], "kind": "sector"}
    for p in base.values():
        if p is not None:
            p["vs_market_pct"] = None
    symbol = bench.get("symbol")
    return base, ({"symbol": symbol, "kind": "benchmark"} if symbol else None)
