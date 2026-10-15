"""Pure technical-indicator math over daily closes — no I/O, no LLM.

Used by ``asset_technicals`` for the Analysis-tab Technicals widget. Each bipolar
row score is a clamped real in [-1, +1]; there is NO combined Buy/Sell mean.
"""

from __future__ import annotations

from typing import Sequence

# Saturation defaults (Option A wireframe). Full scale at ± this magnitude.
_SMA50_SAT = 0.05
_SMA200_SAT = 0.10
_SMA_CROSS_SAT = 0.05
_MACD_HIST_SAT_OF_PRICE = 0.005

ROW_HELP: dict[str, str] = {
    "sma_50": (
        "Average of the last 50 daily closes. Price above it scores toward Buy; "
        "below toward Sell. Full scale at ±5%."
    ),
    "sma_200": (
        "Average of the last 200 daily closes — the long trend line. Same idea as "
        "SMA 50, but full scale at ±10% because this average moves slower."
    ),
    "sma_50_200": (
        "Compares the 50-day average to the 200-day. When the 50 is above the 200 "
        "(a 'golden cross' regime), the marker leans Buy; the reverse ('death cross') "
        "leans Sell. Strength is how far apart they are, not just which is on top."
    ),
    "macd": (
        "MACD is the gap between a fast and slow exponential average; the histogram "
        "is that line minus its signal line. Positive histogram → Buy side; negative → "
        "Sell. Magnitude is relative to price (full scale at 0.5% of the last close)."
    ),
    "rsi": (
        "Relative Strength Index over 14 days — how stretched recent gains or losses "
        "are. 50 is mid-track. High RSI (e.g. 70+) leans Sell (overbought); low RSI "
        "leans Buy (oversold). Continuous, not a hard 30/70 switch."
    ),
    "bollinger": (
        "Where price sits between the upper and lower Bollinger bands (20-day average "
        "± two standard deviations). At the lower band (%B 0) → Buy end; mid → Neutral; "
        "upper band (%B 1) → Sell end. Same stretch idea as RSI, from the band's width."
    ),
}

ROW_LABELS: dict[str, str] = {
    "sma_50": "SMA 50",
    "sma_200": "SMA 200",
    "sma_50_200": "SMA 50 / 200",
    "macd": "MACD",
    "rsi": "RSI (14)",
    "bollinger": "Bollinger %B",
}


def _clamp(n: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, n))


def sma(closes: Sequence[float], period: int) -> list[float | None]:
    """Simple moving average; leading values before ``period`` closes are None."""
    out: list[float | None] = [None] * len(closes)
    if period <= 0:
        return out
    running = 0.0
    for i, c in enumerate(closes):
        running += c
        if i >= period:
            running -= closes[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(closes: Sequence[float], period: int) -> list[float | None]:
    """Exponential moving average; seeds with SMA of the first ``period`` closes."""
    out: list[float | None] = [None] * len(closes)
    if period <= 0 or len(closes) < period:
        return out
    seed = sum(closes[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(closes)):
        prev = closes[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def rsi(closes: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder RSI. Returns None until ``period`` changes are available."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def macd_histogram(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> list[float | None]:
    """MACD line minus signal line (histogram)."""
    fast_e = ema(closes, fast)
    slow_e = ema(closes, slow)
    macd_line: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        if fast_e[i] is not None and slow_e[i] is not None:
            macd_line[i] = fast_e[i] - slow_e[i]  # type: ignore[operator]
    # EMA over the MACD line — skip leading Nones by collecting defined values in order.
    defined = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    hist: list[float | None] = [None] * len(closes)
    if len(defined) < signal:
        return hist
    vals = [v for _, v in defined]
    sig = ema(vals, signal)
    for j, (i, v) in enumerate(defined):
        if sig[j] is not None:
            hist[i] = v - sig[j]  # type: ignore[operator]
    return hist


def bollinger(
    closes: Sequence[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return (mid, upper, lower) Bollinger bands."""
    mid = sma(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        m = mid[i]
        if m is None:
            continue
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        std = var**0.5
        upper[i] = m + num_std * std
        lower[i] = m - num_std * std
    return mid, upper, lower


def pct_b(price: float, upper: float, lower: float) -> float | None:
    width = upper - lower
    if width <= 0:
        return None
    return (price - lower) / width


def score_pct_distance(pct: float, sat: float) -> float:
    if sat <= 0:
        return 0.0
    return _clamp(pct / sat)


def score_rsi(value: float) -> float:
    """50 → 0, 0 → +1 (buy/oversold), 100 → −1 (sell/overbought)."""
    return _clamp((50.0 - value) / 50.0)


def score_bollinger_pct_b(pb: float) -> float:
    """%B 0 → +1, 0.5 → 0, 1 → −1."""
    return _clamp(1.0 - 2.0 * pb)


def build_rows(
    closes: Sequence[float],
    dates: Sequence[str] | None = None,
) -> tuple[list[dict], dict]:
    """Compute the six Technicals rows + chart overlay series from daily closes.

    Returns ``(rows, overlays)`` where each row is::

        {id, label, help, score, raw, raw_display,
         scale_sell, scale_neutral, scale_buy,
         scale_sell_display, scale_neutral_display, scale_buy_display}

    Sell/Neutral/Buy are the raw values at score −1 / 0 / +1 for that indicator's
    bipolar track (not historical min/mean/max). Missing inputs omit that row.
    """
    n = len(closes)
    if n == 0:
        return [], {}
    price = closes[-1]
    s50 = sma(closes, 50)
    s200 = sma(closes, 200)
    hist = macd_histogram(closes)
    rsi_s = rsi(closes, 14)
    _mid, bb_u, bb_l = bollinger(closes, 20, 2.0)

    rows: list[dict] = []

    def _fmt_pct(v: float) -> str:
        return f"{v * 100:+.1f}%"

    def _fmt_macd(v: float) -> str:
        return f"{v:+.2f}"

    def _fmt_rsi(v: float) -> str:
        return f"{v:.0f}"

    def _fmt_pb(v: float) -> str:
        return f"{v:.2f}"

    def _add(
        row_id: str,
        score: float,
        raw: float,
        raw_display: str,
        sell: float,
        neutral: float,
        buy: float,
        fmt,
    ) -> None:
        rows.append(
            {
                "id": row_id,
                "label": ROW_LABELS[row_id],
                "help": ROW_HELP[row_id],
                "score": round(score, 4),
                "raw": raw,
                "raw_display": raw_display,
                "scale_sell": round(sell, 6),
                "scale_neutral": round(neutral, 6),
                "scale_buy": round(buy, 6),
                "scale_sell_display": fmt(sell),
                "scale_neutral_display": fmt(neutral),
                "scale_buy_display": fmt(buy),
            }
        )

    # SMA 50 — % distance series
    pct50: list[float] = []
    for i, v in enumerate(s50):
        if v is not None and v != 0:
            pct50.append((closes[i] - v) / v)
    if pct50:
        raw = pct50[-1]
        _add(
            "sma_50",
            score_pct_distance(raw, _SMA50_SAT),
            raw,
            _fmt_pct(raw),
            -_SMA50_SAT,
            0.0,
            _SMA50_SAT,
            _fmt_pct,
        )

    pct200: list[float] = []
    for i, v in enumerate(s200):
        if v is not None and v != 0:
            pct200.append((closes[i] - v) / v)
    if pct200:
        raw = pct200[-1]
        _add(
            "sma_200",
            score_pct_distance(raw, _SMA200_SAT),
            raw,
            _fmt_pct(raw),
            -_SMA200_SAT,
            0.0,
            _SMA200_SAT,
            _fmt_pct,
        )

    cross: list[float] = []
    for i in range(n):
        a, b, c = s50[i], s200[i], closes[i]
        if a is not None and b is not None and c != 0:
            cross.append((a - b) / c)
    if cross:
        raw = cross[-1]
        _add(
            "sma_50_200",
            score_pct_distance(raw, _SMA_CROSS_SAT),
            raw,
            _fmt_pct(raw),
            -_SMA_CROSS_SAT,
            0.0,
            _SMA_CROSS_SAT,
            _fmt_pct,
        )

    macd_vals = [h for h in hist if h is not None]
    if macd_vals and price != 0:
        raw = macd_vals[-1]
        sat = _MACD_HIST_SAT_OF_PRICE * abs(price)
        _add(
            "macd",
            _clamp(raw / sat) if sat else 0.0,
            raw,
            _fmt_macd(raw),
            -sat,
            0.0,
            sat,
            _fmt_macd,
        )

    rsi_vals = [r for r in rsi_s if r is not None]
    if rsi_vals:
        raw = rsi_vals[-1]
        # Score inverts: 100 → sell (−1), 50 → neutral, 0 → buy (+1).
        _add("rsi", score_rsi(raw), raw, _fmt_rsi(raw), 100.0, 50.0, 0.0, _fmt_rsi)

    pb_vals: list[float] = []
    for i in range(n):
        u, lo = bb_u[i], bb_l[i]
        if u is not None and lo is not None:
            pb = pct_b(closes[i], u, lo)
            if pb is not None:
                pb_vals.append(pb)
    if pb_vals:
        raw = pb_vals[-1]
        _add(
            "bollinger",
            score_bollinger_pct_b(raw),
            raw,
            _fmt_pb(raw),
            1.0,
            0.5,
            0.0,
            _fmt_pb,
        )

    def _series(vals: Sequence[float | None]) -> list[dict]:
        pts: list[dict] = []
        for i, v in enumerate(vals):
            if v is None:
                continue
            d = dates[i] if dates and i < len(dates) else str(i)
            pts.append({"date": d, "value": round(v, 6)})
        return pts

    overlays = {
        "sma_50": _series(s50),
        "sma_200": _series(s200),
        "bb_mid": _series(_mid),
        "bb_upper": _series(bb_u),
        "bb_lower": _series(bb_l),
        "macd": _series(hist),
        "rsi": _series(rsi_s),
    }
    return rows, overlays
