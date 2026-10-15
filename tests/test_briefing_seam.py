"""The AI wealth briefing must run on the INSTANCE-CONFIGURED model via the
``core.llm_seam`` provider seam — NOT a raw Anthropic SDK call with a hardcoded model id.

Since the migration to the structured-output layer, the briefing routes its completion
through ``ai.structured.complete_model`` (schema-derived json_object mode + validate/reask)
with a ``_BriefingOutput`` Pydantic model. ``complete_model`` calls ``core.llm_seam`` under
the hood, so these tests still stub the seam's ``_complete`` hook (no network) and assert:
  * the completion routes through the seam and passes the model resolved by ``get_model()``
    (so it works under anthropic OR openai_compatible — no hardcoded Anthropic-only client/id);
  * the output shape (``note``/``actions``/``chat_suggestion``) is preserved;
  * a seam failure (e.g. ``LLMUnavailable`` under a mis-/un-configured provider) degrades to
    a visible error fallback rather than raising;
  * an unparseable/unusable reply (no valid structured output after a reask) SKIPS to the
    error fallback rather than shipping raw/empty output;
  * the day-part cache round-trips a saved payload.

The seam stubs MUST accept ``**kw`` — ``complete_model`` forwards ``response_format`` and
``effort`` into ``_complete``, so a text-only stub signature would break.

Run:
    .venv/bin/pytest tests/test_briefing_seam.py -v
"""

from __future__ import annotations

import json

import pytest

import backend.api.briefing as briefing
import backend.core.llm_seam as llm_seam


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Repoint the cache at tmp + neutralize the data-reading context builder so the test
    never touches live data or the network."""
    monkeypatch.setattr(briefing, "CACHE_FILE", tmp_path / "briefing_cache.json")
    monkeypatch.setattr(briefing, "_build_context", lambda total_nw, book=None: "CONTEXT\n")
    # No market rows → the news-fetch branch is skipped (no search-provider call).
    monkeypatch.setattr(briefing, "_invested_book", lambda base: {"merged_rows": [], "rows": []})
    monkeypatch.setattr(briefing, "read_profile", lambda: {})
    monkeypatch.setattr(briefing.ledger_service, "base_currency", lambda: "USD")
    # The structured layer runs reasoning-off + json_object mode: the seam falls back to a
    # plain json.loads (returning None on failure) so a canned JSON string round-trips.
    monkeypatch.setattr(llm_seam, "_parse_json", None)


@pytest.mark.asyncio
async def test_briefing_uses_configured_model_via_seam(monkeypatch):
    """The completion goes through the seam with the model from get_model() — proving it is
    NOT a hardcoded Anthropic client/id (works for any configured provider)."""
    captured: dict = {}

    async def _complete(prompt, *, system="", model="", max_tokens=2048, source="complete", **kw):
        captured["model"] = model
        captured["source"] = source
        captured["system"] = system
        return json.dumps(
            {
                "note": "All quiet this morning.",
                "actions": ["Review the budget."],
                "chat_suggestion": "How is my runway?",
            }
        )

    # A provider-agnostic configured model id (here a non-Anthropic-shaped one to make the
    # "no hardcoded claude id" point concrete).
    monkeypatch.setattr(briefing, "get_model", lambda: "openai-compatible-model-x")
    monkeypatch.setattr(llm_seam, "_complete", _complete)

    out = await briefing._generate_briefing(123456.0)

    assert captured["model"] == "openai-compatible-model-x"
    assert captured["source"] == "briefing"
    assert out["note"] == "All quiet this morning."
    assert out["actions"] == ["Review the budget."]
    assert out["chat_suggestion"] == "How is my runway?"


@pytest.mark.asyncio
async def test_briefing_degrades_when_provider_unavailable(monkeypatch):
    """An unconfigured/erroring provider (LLMUnavailable) must NOT raise — the route returns
    the fallback note."""
    from backend.core.llm_seam import LLMUnavailable

    async def _boom(prompt, **kwargs):
        raise LLMUnavailable("no provider")

    monkeypatch.setattr(briefing, "get_model", lambda: "")
    monkeypatch.setattr(llm_seam, "_complete", _boom)

    out = await briefing._generate_briefing(0.0)
    # Degrades to a visible, specific error placeholder (not a raise, not a generic note).
    assert out["error"] is True
    assert "briefing" in out["note"].lower()
    assert out["chat_suggestion"] == ""


@pytest.mark.asyncio
async def test_briefing_skips_when_reply_unparseable(monkeypatch):
    """A reply that never yields valid structured output (even after the reask) must SKIP to
    the visible error fallback — never ship a raw/empty body."""

    async def _complete(prompt, **kwargs):
        return "not json at all — just prose the model rambled out"

    monkeypatch.setattr(briefing, "get_model", lambda: "claude-haiku-4-5")
    monkeypatch.setattr(llm_seam, "_complete", _complete)

    out = await briefing._generate_briefing(123456.0)
    assert out["error"] is True
    assert out["chat_suggestion"] == ""
    # No fabricated note leaked through — it's the explicit error placeholder.
    assert "couldn't read" in out["note"].lower()


@pytest.mark.asyncio
async def test_briefing_survives_no_search_provider(monkeypatch, db_isolated):
    """Regression: when the news branch runs but no usable web-search provider is configured,
    ``get_search_provider()`` raises — the briefing must degrade to no news (empty block) and
    still generate, NOT 500 the whole route."""
    captured: dict = {}

    # Force the news-fetch branch: a market-exposed holding + holdings_news preference on.
    monkeypatch.setattr(
        briefing,
        "_invested_book",
        lambda base: {
            "merged_rows": [
                {
                    "symbol": "AAPL",
                    "name": "Apple",
                    "value": 200.0,
                    "qty": 1,
                    "price": 200.0,
                    "currency": "USD",
                    "weight": 100.0,
                    "day_pct": 0.0,
                }
            ]
        },
    )
    monkeypatch.setattr(briefing, "read_profile", lambda: {"base_currency": "USD"})

    def _no_provider():
        raise ValueError("no usable web-search provider configured")

    # briefing now resolves news through the shared collector (monitoring.collect.gather_evidence),
    # which resolves the provider via backend.ai.search.get_search_provider — patch it there.
    monkeypatch.setattr("backend.monitoring.collect.get_search_provider", _no_provider)

    async def _complete(prompt, **kwargs):
        captured["prompt"] = prompt
        return json.dumps(
            {"note": "Generated despite no search.", "actions": [], "chat_suggestion": ""}
        )

    monkeypatch.setattr(briefing, "get_model", lambda: "claude-haiku-4-5")
    monkeypatch.setattr(llm_seam, "_complete", _complete)

    out = await briefing._generate_briefing(123456.0)

    # The provider error did not propagate; the briefing generated.
    assert out["note"] == "Generated despite no search."
    # The news block degraded to empty (no fabricated headlines).
    assert "CURRENT NEWS:\n" in captured["prompt"]


@pytest.mark.asyncio
async def test_briefing_caches_after_generation(monkeypatch):
    """A generated payload is saved + reloaded within the same day-part (cache preserved)."""

    async def _complete(prompt, **kwargs):
        return json.dumps({"note": "Cached note.", "actions": [], "chat_suggestion": ""})

    monkeypatch.setattr(briefing, "get_model", lambda: "claude-haiku-4-5")
    monkeypatch.setattr(llm_seam, "_complete", _complete)

    payload = await briefing._generate_briefing(0.0)
    briefing._save_cache(payload)

    cached = briefing._load_cache()
    assert cached is not None
    assert cached["note"] == "Cached note."
