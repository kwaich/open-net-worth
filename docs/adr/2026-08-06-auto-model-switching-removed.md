# 2026-08-06 — Automatic model switching is removed; a turn runs the default or your explicit pick

**Status:** Accepted (supersedes `2026-07-11-cross-provider-routing-engine`; narrows
`2026-07-18-model-picker-capability-filter-and-user-allowlist` to its picker half)
**Owner:** the maintainer (decision stated in-session: *"mo[d]els are getting cheaper, and I realize
that most of the time, I just need to stay with one model"*)
**Touches:** DELETED `backend/ai/routing/` (`engine`, `scoring`, `candidates`, `classifier`,
`delegation`, `session`, `types`), `backend/db/routing_config_store.py`,
`backend/db/routing_log_store.py`, `backend/api/routing_config.py`, `backend/ai/tools/routing.py`,
`frontend/src/components/tabs/settings/RoutingPanel.tsx`,
`frontend/src/components/tabs/settings/PreferredModelsSection.tsx`.
NEW `backend/db/preferred_models_store.py`, `backend/api/preferred_models.py`,
`backend/scripts/migrate_drop_auto_model_switching.py`.
CHANGED `backend/api/chat.py`, `backend/ai/assistant.py`,
`backend/ai/automation_runner_adapter.py`, `backend/api/models.py`,
`backend/connectors/telegram/handlers.py`, `backend/services/automations/{options,inference}.py`,
`backend/core/paths.py`, `backend/ai/checkpoint.py`, `backend/main.py`,
`frontend/src/components/tabs/settings/sections/ModelSection.tsx`,
`frontend/src/components/chat/ChatPanel.tsx`,
`frontend/src/components/chat/lucius/{ModelEffortRow,LuciusDock}.tsx`,
`frontend/src/hooks/useModels.ts`,
`frontend/src/components/tabs/automations/AutomationEditorModal.tsx`

## Context

`2026-07-11-cross-provider-routing-engine` shipped Adaptive Model Selection: an `"auto"`
sentinel stored as a conversation's model, which — on every turn — classified the turn into a
weak/strong tier, built a cross-provider candidate pool, scored it against a user-chosen
profile (`cheapest` / `balanced` / `fastest`) and measured latency telemetry, honoured per-tier
pins and a risk guard, and logged the decision for explainability.

It was **the default** for every new conversation, and it carried a real surface:

- a store (`routing_config.json`: profile, allow_local, risk_guard, pins, model_allowlist),
- a REST pair (`GET/PUT /api/routing-config`),
- two AI tools (`get_routing_config`, `update_routing_config`),
- a decision log (`routing_decisions.json`),
- a Settings panel (profile radios, allow-local switch, two pin selects, a
  "Last routing decision" readout),
- a sticky per-session `routed_tier`, an `auto_routed`/`routing` stamp on every usage event,
  a defensive `model == "auto"` guard in `assistant.run`, and its own branch in the
  Automation runner.

**The premise it was built on has moved.** The engine's whole value was arbitrage: route the
cheap turns to a cheap model so the flagship is spent only where it earns its keep. Model
prices have fallen far enough that the saving no longer pays for the machinery — and the
owner's actual usage pattern is one model, most of the time. What remained was a system that
made every turn's model **non-deterministic and hard to predict**, for a shrinking benefit.

The one part that earned its place independently is the **preferred-model shortlist**
(`model_allowlist`, from `2026-07-18`). Its picker half — a "Preferred" group pinned atop a
list that can otherwise run to 300+ OpenRouter entries — is useful with or without routing.
Its routing half (the pool Auto's weak tier chose among) dies with the engine.

## Decision

**A chat turn runs exactly one of two models: the instance default, or the model the user
explicitly picked for that conversation.** There is no third, inferred option.

1. **The `"auto"` sentinel is gone.** Not deprecated, not tolerated — removed from the picker,
   from `resolveModelOverride`, from `assistant.run`'s guard, from the Automation runner, and
   from stored data (see Migration). A conversation's model is `""` (instance default) or a
   concrete `"<provider_key>:<model_id>"` ref.
2. **The routing engine and its config are deleted**, along with the decision log, the
   Settings panel, and both AI tools.
3. **Preferred models survive, in their own home.** `PreferredModelsStore`
   (`preferred_models.json`, `{"models": [...]}`) behind `GET/PUT /api/preferred-models`,
   surfaced as a top-level "Preferred models" panel in Settings → AI Models. It is a **display
   shortlist only** — it selects nothing, it orders the picker.

### Deliberate sub-decisions

- **The two AI tools are deleted with NO alias**, against the standing rule in CLAUDE.md that
  a tool name is persisted data. The rule exists so a rename does not break an automation or
  an in-flight transcript that stored the old name — but here there is no successor to alias
  *to*: the preferred list is a UI-only concern and Lucius has no business re-pointing how
  models get chosen when only one thing chooses them (the owner). An automation that called
  `update_routing_config` now gets an unknown-tool error, which is the honest outcome.
- **Model telemetry is KEPT** (`model_telemetry.json` + the `_record_model_telemetry` hook in
  `assistant.py`). Its only consumer was the engine's scoring, so it is now write-only — kept
  as raw observability at the owner's call, and still checkpoint-EXCLUDED (it writes on every
  model turn; without that exclusion every Q&A turn mints a checkpoint — the 2026-07-11
  regression that `test_checkpoint_runtime_exclusions.py` pins).
- **An Automation's default model is `""` (instance default), not `"auto"`.** The editor's
  first option is now "Default".
- **`routing_decisions.json` loses its checkpoint exclusion** because the file no longer
  exists. `MODEL_TELEMETRY_JSON` keeps its own.

## Migration

`backend/scripts/migrate_drop_auto_model_switching.py`, wired as a best-effort boot hook in
`main.py` (the `migrate_drop_payee_txn_count` pattern) and runnable standalone with a
dry-run default. It is idempotent and does three things:

1. Lifts `model_allowlist` out of `routing_config.json` into `preferred_models.json`, then
   deletes the legacy file. **Skipped when the target already exists** — a second run must
   never clobber a list the owner has since edited.
2. Rewrites chat sessions whose stored `model` is `"auto"` → `""`, and drops the stale
   `routed_tier` field.
3. Rewrites automations whose stored `model` is `"auto"` → `""`.

A one-time migration was chosen over a permanent read-time coercion (owner's call): nothing
resolves `"auto"` any more, so a row left carrying it would be sent to a provider as a literal
model id. Clearing the data means no code has to keep tolerating a sentinel that no longer
exists.

## Consequences

**Good.** Every turn's model is predictable and visible in the picker. ~900 lines of routing
machinery, a store, a log, a REST pair, two AI tools and a Settings panel are gone. The
tool-schema budget drops by two tools. No per-turn classification work before the first token.

**Costs, accepted.**

- Cheap turns now cost flagship prices unless the owner switches models by hand. That is the
  trade being made knowingly — it is the arbitrage this ADR judges no longer worth its
  complexity.
- The Automation runner no longer resolves a per-run model; an Automation stores a concrete
  model or inherits the instance default.
- `ONW_AUTO_WEAK_MODEL` is no longer read by anything.
- A caller of `GET/PUT /api/routing-config` gets a 404. Self-hosted instances that scripted
  against it must move to `/api/preferred-models` (different shape: `{"models": [...]}`,
  PUT replaces wholesale).

**Reversal.** The engine is recoverable from git history, but its config would have to be
rebuilt — `preferred_models.json` deliberately does not retain `profile`/`allow_local`/
`risk_guard`/`pins`. That is intentional: a half-kept config is worse than none.

## Agent provenance

Decided by the repo owner and implemented by a Claude agent in a single session. The owner
opened with the removal already decided — *"i want to remove the 'auto' mode from our
implementation for model selection … we shouldn't have that logic, shouldn't have settings,
shouldn't have it as default; only default and preferred models are retaining"* — and, mid-run,
disambiguated the target: this is auto **model switching**, not the Automation `approval: "auto"`
mode, which is untouched (`backend/ai/_confirm_gate.py`, `services/automations/service.py`).

Four sub-decisions were surfaced back to the owner rather than assumed, because each changed the
shape of the work: where preferred models live (new store + migration, over renaming nothing),
what happens to stored `"auto"` rows (one-time migration, over a permanent read-time shim),
whether model telemetry survives (kept), and whether the two AI tools get rename aliases
(deleted outright). The alias question is the one that bends a stated CLAUDE.md rule; the
reasoning is in **Deliberate sub-decisions** above.
