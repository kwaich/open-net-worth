"""Unit tests for Analysis-tab technical indicator math (no I/O)."""

from __future__ import annotations

from backend.services import indicators as ind


def _ramp(n: int, start: float = 100.0, step: float = 0.5) -> list[float]:
    return [start + i * step for i in range(n)]


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals)


def test_sma_needs_full_window():
    closes = _ramp(10)
    out = ind.sma(closes, 5)
    assert out[:4] == [None, None, None, None]
    assert out[4] == _mean(closes[:5])
    assert out[-1] == _mean(closes[-5:])


def test_sma50_score_saturation_example():
    """Wireframe: +3.1% vs SMA50 → score +0.62 at 5% saturation."""
    assert abs(ind.score_pct_distance(0.031, 0.05) - 0.62) < 1e-9


def test_sma200_score_saturation_example():
    """Wireframe: +8.4% vs SMA200 → score +0.84 at 10% saturation."""
    assert abs(ind.score_pct_distance(0.084, 0.10) - 0.84) < 1e-9


def test_rsi_score_70_is_minus_0_4():
    assert abs(ind.score_rsi(70.0) - (-0.4)) < 1e-9


def test_bollinger_pct_b_ends():
    assert ind.score_bollinger_pct_b(0.0) == 1.0
    assert ind.score_bollinger_pct_b(0.5) == 0.0
    assert ind.score_bollinger_pct_b(1.0) == -1.0


def test_build_rows_omits_sma200_when_series_short():
    closes = _ramp(60)  # enough for SMA50 / RSI / BB / MACD-ish, not SMA200
    rows, overlays = ind.build_rows(closes, [f"2024-01-{i + 1:02d}" for i in range(60)])
    ids = {r["id"] for r in rows}
    assert "sma_50" in ids
    assert "sma_200" not in ids
    assert "sma_50_200" not in ids
    assert "rsi" in ids
    assert "bollinger" in ids
    assert overlays["sma_50"]


def test_build_rows_six_when_long_enough():
    closes = _ramp(220)
    dates = [f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(220)]
    rows, overlays = ind.build_rows(closes, dates)
    ids = [r["id"] for r in rows]
    assert ids == ["sma_50", "sma_200", "sma_50_200", "macd", "rsi", "bollinger"]
    assert all(-1.0 <= r["score"] <= 1.0 for r in rows)
    assert rows[2]["label"] == "SMA 50 / 200"
    # Sell / Neutral / Buy are scale anchors (not historical min/mean/max).
    by_id = {r["id"]: r for r in rows}
    assert by_id["sma_50"]["scale_sell_display"] == "-5.0%"
    assert by_id["sma_50"]["scale_neutral_display"] == "+0.0%"
    assert by_id["sma_50"]["scale_buy_display"] == "+5.0%"
    assert by_id["sma_200"]["scale_sell_display"] == "-10.0%"
    assert by_id["sma_200"]["scale_buy_display"] == "+10.0%"
    assert by_id["rsi"]["scale_sell_display"] == "100"
    assert by_id["rsi"]["scale_neutral_display"] == "50"
    assert by_id["rsi"]["scale_buy_display"] == "0"
    assert by_id["bollinger"]["scale_sell_display"] == "1.00"
    assert by_id["bollinger"]["scale_neutral_display"] == "0.50"
    assert by_id["bollinger"]["scale_buy_display"] == "0.00"
    for r in rows:
        assert "scale_low" not in r
        assert "scale_mean" not in r
        assert "scale_high" not in r
        assert r["scale_sell_display"]
        assert r["scale_neutral_display"]
        assert r["scale_buy_display"]
        assert r["raw_display"]
    for key in ("sma_50", "sma_200", "bb_mid", "bb_upper", "bb_lower", "macd", "rsi"):
        assert overlays[key], f"missing overlay {key}"


def test_build_rows_empty():
    rows, overlays = ind.build_rows([])
    assert rows == []
    assert overlays == {}
