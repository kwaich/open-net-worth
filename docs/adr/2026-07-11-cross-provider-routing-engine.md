# 2026-07-11 — Cross-provider auto-routing engine (profiles, risk guard, measured latency)

**Status:** **Superseded (2026-08-06)** by
[`2026-08-06-auto-model-switching-removed`](2026-08-06-auto-model-switching-removed.md) — the
engine, its config store, its decision log, its Settings panel and both AI tools are DELETED;
only the preferred-model shortlist survives (moved to `preferred_models.json`). Model prices fell
far enough that the cheap-turn arbitrage no longer paid for the machinery. Everything below is
the historical record of a system that no longer exists.
Originally: accepted (owner-directed: "strong foundation, build the right thing") · implements
`.claude/rfcs/cross-provider-auto-routing.md` · supersedes the single-provider AMS weak map
**Where:** `backend/ai/routing/` (engine package), `backend/db/routing_config_store.py` /
`model_telemetry_store.py` / `routing_log_store.py`, `backend/api/routing_config.py`,
`backend/ai/tools/routing.py`, `backend/api/chat.py` (adapter), providers stamp
`ttft_ms`/`duration_ms`, `backend/core/agent_caps.py`+`headless_scope.py` (T0 read),
UI spec `.claude/rfcs/routing-panel-ui-spec.md` (panel NOT yet built — design-first gate)

## Decision

"Auto" model selection is a **routing engine**, not an if-chain in the chat router:
`route_turn(TurnSignals) → RoutingDecision` orchestrating classify → candidates → score →
delegate → decide, with every decision recorded (last 200, cache, checkpoint-excluded).

1. **Engine lives in `backend/ai/routing/`, NOT `services/`** — it consumes
   `ai.models` pricing/capability data; `services` importing `ai` would break the layer
   contract. The tier heuristic itself stays in `services/model_router.py` (unchanged) and
   is composed by the engine's classifier. RFC amended accordingly.
2. **Weak tier routes cross-provider from a CURATED pool** — each configured+enabled
   provider contributes its known-good tool-capable cheap model (`WEAK_BY_CATalog`-style
   map now in `candidates.py`); local runtimes/gateways contribute the user's own
   configured model; capability-checked against the vendored map
   (`supports_function_calling`, now retained by `make refresh-prices`). No
   price-threshold auto-discovery (false economies / broken tool loops).
3. **Latency is MEASURED, never assumed** — providers stamp ttft/duration on usage
   events; `assistant.run` feeds a rolling per-model window (`model_telemetry.json`).
   Cold-start conservatism: an unmeasured LOCAL model is ineligible under
   balanced/fastest (it earns eligibility by measurement); the unmeasured-cloud default
   is deliberately pessimistic (5000 ms) so a measured model always outranks a guess.
4. **Profiles are the user contract** — cheapest / balanced (default) / fastest
   (OpenRouter's `:floor`/`:nitro` mental model), plus `allow_local`, `risk_guard`, and
   per-tier pins (`routing_config.json`, portable). Env `ONW_AUTO_WEAK_MODEL` remains the
   top-priority operator pin; pins never resurrect a disabled provider.
5. **Risk guard** — money/write-intent turns (transfer/pay/buy/sell/delete/confirm/…,
   currency amounts) never route weak, regardless of profile (Hermes/Lynkr's
   risk-analyzer lesson). Deliberately broad: a false "strong" costs pennies.
6. **Strong stays home by policy** — the strong tier anchors to the instance default
   (prompt caches are model-scoped; roaming re-pays cold cache writes). One branch in the
   engine, not scattered hardcode.
7. **Delegation** — we route BETWEEN providers; OpenRouter (`openrouter/auto`, `:floor`/
   `:nitro` suffixes) and LiteLLM proxies route WITHIN themselves.
8. **Decisions are recorded + ridden on usage** (`usage.routing = {tier, reason,
   profile}`) — explainability now, the learning substrate a future trained router needs.

## Consequences

- Weak picks are now PROVIDER-PINNED refs (`"anthropic:claude-haiku-4-5"`), no longer
  bare ids — tests updated; `assistant.run`'s override contract already handled both.
- Free local Ollama serves cheap turns once it has proven fast on the user's hardware
  (or immediately under "cheapest", speed-floored at 15 tok/s when measured).
- Two new AI tools (`get_routing_config`* T0/headless-read, `update_routing_config`†);
  tool-schema budget raised 280k→283k (commented in the test).
- The Settings "Model routing" panel ships separately (spec ready, UI Quality Gate
  applies); until then the config is REST/tool-reachable (AI-native-first, per the
  2026-07-08 design test).
