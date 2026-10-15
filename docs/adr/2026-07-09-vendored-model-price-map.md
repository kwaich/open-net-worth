# 2026-07-09 — Vendored model price map (borrow LiteLLM's data, not the library)

**Status:** accepted (owner-directed) · augments `backend/ai/models/pricing.py`
**Where:** `backend/ai/models/price_map.py` (new), `backend/ai/models/model_prices.json`
(vendored data), `scripts/refresh_model_prices.py` (new), `Makefile` (`refresh-prices`),
`backend/ai/models/pricing.py` (`cost_usd_openai` fallback)
**Spec/context:** `docs/superpowers/specs/2026-07-09-additional-ai-providers-design.md`
(this ADR is the follow-on after adding xAI/GLM/OpenRouter/Custom providers surfaced
that hand-typed pricing goes stale — the tables shipped 6 months behind real models).

## Decision

Model pricing resolves in **three layers**, most-authoritative first:

1. **Hand-typed tables** in `pricing.py` (`_ANTHROPIC_PRICING`, `_OPENAI_PRICING`) —
   PINNED overrides for our core/default models and any bleeding-edge model upstream
   data still lags on (e.g. `grok-4.5`, `glm-5.2` — both absent from the current
   upstream map). These win, are unit-tested, and give deterministic numbers.
2. **Vendored price map** (`price_map.vendored_rates`) — a filtered copy of LiteLLM's
   public `model_prices_and_context_window.json`, consulted only when the hand tables
   miss. Covers the long tail of models a user might pick (DeepSeek, Moonshot, older
   Grok/GLM, GPT/Gemini variants, …) so they price from a community-maintained source
   instead of showing `$0` unmeasured.
3. **Unmeasured** — when both miss, `cost_usd=0.0` with the existing WARNING log.

We **borrow the DATA, not the library.** No `litellm` dependency is added: the vendored
JSON is a static data file read with stdlib `json`, loaded once (`lru_cache`). Refresh is
a **manual maintenance** step (`make refresh-prices` → `scripts/refresh_model_prices.py`),
never a runtime/network call in the request path. The refresh script filters upstream to
text-generation rows (`chat`/`completion`/`responses`) and keeps only the pricing fields
we use, writing a compact one-model-per-line file (~320 KB, ~2.2k models) for clean diffs.

Lookup normalizes keys across conventions: exact bare id, `"<provider>/<model>"` built
from a base_url→provider hint (`api.x.ai`→`xai`, `z.ai`/`bigmodel.cn`→`zai`,
`deepseek.com`→`deepseek`, …), and prefix-stripped tail. Upstream **per-token** costs are
converted to the tables' **per-million** units.

## Why (and why not full LiteLLM)

The pain was hand-maintained *numbers* going stale, not the *transport*. Every comparable
self-hosted agent (Hermes Agent's `ProviderTransport` ABC, OpenClaw's `registerProvider`
plugins, LibreChat, us) hand-rolls the provider/streaming/tool-loop layer for control over
tool-calling, prompt-cache TTLs, and cost-context; only coding agents (OpenHands, Aider)
route everything through LiteLLM. Full LiteLLM adoption would mean rewriting our normalized
event stream, surface-aware Anthropic cache breakpoints, and batch/cost seams, plus a large
fast-moving dependency and wider audit surface — high risk, low fit. Sourcing just the price
*data* fixes the actual pain with no dependency and no hot-path coupling, keeping `core` a
stdlib-only leaf and the transport layer ours.

## Consequences

- Any OpenAI-compatible model in the upstream map now prices automatically — no code change
  per new model. Refresh with `make refresh-prices` to pick up new models/prices.
- Hand tables remain the pin for determinism/tests and for models newer than upstream; they
  never get overridden by the vendored map (fallback-only). A stale hand price would still
  need a manual edit — acceptable, since the set is tiny and tested.
- OpenRouter is unaffected: its real per-request cost is captured from the response
  (`usage.cost`); the map is not consulted for it.
- Accepted divergence: two former tests that asserted `deepseek-* → $0 (unknown)` now expect
  a real non-zero cost (the map covers them). Updated in `tests/test_openai_pricing.py`.
