"""An OpenAI-compatible (OpenRouter) model client for the tool-surface harness.

Kept OUT of ``model_client.py`` on purpose: that file is owned by another lane on
this branch. This module is purely ADDITIVE — a second ``ModelClient`` implementation
that plugs into ``runner.run_one`` via its existing ``client_factory`` parameter, no
change to the harness itself required.

Rather than hand-rolling tool-schema translation and cost accounting for a second
provider, this wraps the REAL production ``backend.ai.models.openai_provider.OpenAIProvider``
— the same class the live app uses for every ``openai_compatible`` model (OpenRouter,
xAI, local Ollama, ...). That buys three things for free and keeps them from silently
drifting versus what the app actually does:

  * Anthropic-shaped tool schemas -> OpenAI function-calling schemas (``_tools_to_openai``)
  * Anthropic-shaped message history -> OpenAI chat messages (``_messages_to_openai``)
  * REAL per-request cost: OpenRouter fronts hundreds of namespaced upstream models, so
    a static price-per-model table cannot estimate spend (confirmed empirically here —
    ``backend.ai.models.pricing.cost_usd_for_model`` returns 0.0 "unmeasured" for both
    ``minimax/minimax-m3`` and ``z-ai/glm-5.2`` because neither the hand-pinned tables nor
    the vendored LiteLLM map key on OpenRouter's bare ``vendor/model`` id). The production
    provider already asks OpenRouter to report the real dollar cost per call
    (``usage: {include: true}`` -> ``usage.cost``) and this client trusts that figure,
    exactly as production does.

A fresh ``OpenAIProvider`` (and therefore a fresh ``AsyncOpenAI`` / httpx client) is
constructed on every ``run_turn`` call rather than reused across calls. ``runner.run_one``
invokes ``client.run_turn`` synchronously, once per round trip, each wrapped in its own
``asyncio.run(...)``; reusing one httpx client's connection pool across independently
closed event loops is a known source of "attached to a different loop" failures. The
per-call construction cost (a new connection) is negligible next to an LLM round trip.
"""

from __future__ import annotations

import asyncio

from backend.evals.tool_surface.model_client import ModelClient, ToolCall, TurnResponse


class OpenRouterClient(ModelClient):
    """Speaks to any OpenAI-compatible endpoint (OpenRouter by default) as a ModelClient."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
    ) -> None:
        if not api_key:
            raise RuntimeError(f"no API key available for openai_compatible model {model!r}")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.name = f"openrouter:{model}"
        #: cumulative REAL dollar cost this client has observed, so the driver can take
        #: a before/after snapshot around one run and know exactly what it spent — the
        #: harness's own ``compute_cost_usd`` cannot price this model (see module doc).
        self.total_reported_cost_usd = 0.0
        #: how many calls had NO reported cost (endpoint didn't return usage.cost) —
        #: surfaced so a run's cost figure can be labelled honestly if this is ever > 0.
        self.unreported_calls = 0
        self.calls = 0

    def run_turn(self, system: str, messages: list[dict], tools: list[dict]) -> TurnResponse:
        return asyncio.run(self._run_turn_async(system, messages, tools))

    async def _run_turn_async(
        self, system: str, messages: list[dict], tools: list[dict]
    ) -> TurnResponse:
        from backend.ai.models.openai_provider import OpenAIProvider

        provider = OpenAIProvider(base_url=self.base_url, api_key=self.api_key)

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        input_tokens = output_tokens = cache_read_tokens = 0
        cost_usd: float | None = None
        stop_reason = "end_turn"

        async for event in provider.stream_chat(messages, tools, system, self.model):
            etype = event.get("type")
            if etype == "text_delta":
                text_parts.append(event.get("text", ""))
            elif etype == "tool_use":
                calls.append(
                    ToolCall(id=event["id"], name=event["name"], input=event.get("input") or {})
                )
            elif etype == "usage":
                input_tokens = event.get("input_tokens", 0) or 0
                output_tokens = event.get("output_tokens", 0) or 0
                cache_read_tokens = event.get("cache_read_tokens", 0) or 0
                cost_usd = event.get("cost_usd")
            elif etype == "done":
                stop_reason = event.get("stop_reason") or "end_turn"

        self.calls += 1
        if cost_usd:
            self.total_reported_cost_usd += cost_usd
        else:
            self.unreported_calls += 1

        return TurnResponse(
            text="".join(text_parts).strip(),
            tool_calls=calls,
            # Mirrors the Anthropic convention: input_tokens excludes the cached subset,
            # which rides separately in cache_read_tokens.
            input_tokens=max(0, input_tokens - cache_read_tokens),
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            stop_reason=stop_reason,
            # These are real API-measured counts (OpenRouter reports usage every call),
            # never the mock's chars//4 guess.
            estimated=False,
        )
