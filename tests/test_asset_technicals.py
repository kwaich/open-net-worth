"""Unit tests for technicals lean helpers + narrative guards (no live LLM)."""

from __future__ import annotations

import pytest

from backend.services import asset_technicals as at


def test_lean_from_scores_buy():
    rows = [{"id": "a", "score": 0.5}, {"id": "b", "score": 0.4}]
    assert at.lean_from_scores(rows) == "leans_buy"
    assert at.lean_phrase("leans_buy") == "leans buy"
    assert at.lean_label("leans_buy") == "Leans buy"


def test_lean_from_scores_sell():
    rows = [{"id": "a", "score": -0.5}, {"id": "b", "score": -0.3}]
    assert at.lean_from_scores(rows) == "leans_sell"


def test_lean_from_scores_mixed():
    rows = [{"id": "a", "score": 0.1}, {"id": "b", "score": -0.1}]
    assert at.lean_from_scores(rows) == "mixed"
    assert at.lean_from_scores([]) == "mixed"


def test_contradict_blocks_personal_advice():
    rows = [{"id": "rsi", "score": -0.4, "raw_display": "70"}]
    assert at._rows_contradict_paragraph(rows, "You should buy this name.") is True
    assert at._rows_contradict_paragraph(rows, "We recommend buying here.") is True


def test_contradict_allows_lean_language():
    rows = [{"id": "rsi", "score": -0.4, "raw_display": "70"}]
    assert at._rows_contradict_paragraph(rows, "On balance this leans buy.") is False
    assert at._rows_contradict_paragraph(rows, "On balance this leans sell.") is False
    assert at._rows_contradict_paragraph(rows, "On balance this is mixed.") is False


def test_bundle_hash_includes_prompt_version():
    rows = [{"id": "rsi", "score": -0.4, "raw_display": "70"}]
    h1 = at._bundle_hash(rows)
    assert at._bundle_hash(rows) == h1
    assert at.PROMPT_VERSION == "v3-grounded"


def test_paragraph_invents_figures_rejects_wrong_macd():
    rows = [
        {"id": "macd", "score": 0.5, "raw_display": "+0.99"},
        {"id": "rsi", "score": 0.0, "raw_display": "50"},
    ]
    good = "On balance this leans buy. MACD histogram at +0.99 with RSI at 50."
    bad = "On balance this leans buy. MACD histogram at +0.58 with RSI at 50."
    assert at._paragraph_invents_figures(rows, good) is False
    assert at._paragraph_invents_figures(rows, bad) is True


def test_paragraph_allows_gloss_period_numbers():
    rows = [{"id": "sma_50", "score": -0.18, "raw_display": "-0.9%"}]
    text = (
        "On balance this leans sell. Price is -0.9% below its 50-day average "
        "and still under the 200-day average."
    )
    assert at._paragraph_invents_figures(rows, text) is False


@pytest.mark.asyncio
async def test_synthesize_rejects_invented_macd(monkeypatch):
    rows = [
        {
            "id": "macd",
            "label": "MACD",
            "score": 0.5,
            "raw_display": "+0.99",
        },
        {
            "id": "rsi",
            "label": "RSI (14)",
            "score": 0.0,
            "raw_display": "50",
        },
    ]

    async def _fake_complete_json(*_a, **_k):
        return {
            "paragraph": (
                "On balance this leans buy. MACD momentum at +0.58 while "
                "relative strength (RSI) sits at 50."
            )
        }

    monkeypatch.setattr(
        "backend.core.llm_seam.complete_json",
        _fake_complete_json,
    )
    assert await at._synthesize(rows) is None
