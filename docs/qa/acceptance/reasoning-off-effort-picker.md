# Acceptance — "Off" in the per-turn reasoning-effort picker

Covers: an honest "Off" (reasoning fully disabled) option in the chat effort dropdown,
shown only for models that can genuinely disable reasoning (OpenRouter reasoning models),
plus the background reasoning-off wiring on mechanical tasks.

Related: `docs/adr/2026-07-20-disable-reasoning-on-mechanical-tasks-and-honest-off-picker.md`

## Backend capability

- **GIVEN** an OpenRouter reasoning model (e.g. `z-ai/glm-5.2`, whose `supported_efforts`
  lists only `high`/`xhigh`)
  **WHEN** `/api/models` builds its row
  **THEN** the row carries `can_disable: true` (keyed on reasoning-support + OpenRouter
  transport, NOT on a `"none"` effort rung being advertised).

- **GIVEN** an OpenAI reasoning family model (`o3-mini`, `gpt-5`), an xAI reasoning model
  (`grok-4.5`), an Anthropic model (`claude-fable-5`), or a local/unknown endpoint
  **WHEN** `/api/models` builds its row
  **THEN** the row carries `can_disable: false` (none of these deliver a true off via
  `effort="none"` — OpenAI maps to `minimal`, Anthropic coerces to `medium`, xAI/local
  are no-ops).

- **GIVEN** an OpenRouter NON-reasoning model (`supported_parameters` without `reasoning`)
  **THEN** `can_disable: false` (nothing to disable).

## Effort picker (ModelEffortRow + ChatPanel)

- **GIVEN** the active chat model has `can_disable: true`
  **WHEN** the effort dropdown renders
  **THEN** it shows a leading **Off** option before the model's real effort tiers
  (e.g. `Off`, `High`, `Xhigh`).

- **GIVEN** the user selects **Off**
  **THEN** `chat-effort` in localStorage becomes `"off"`, the dropdown shows `Off`, and the
  next turn's request sends `effort: "off"` (the provider translates it to
  `reasoning: {"enabled": false}` for OpenRouter → 0 reasoning tokens).

- **GIVEN** the active model has `can_disable: false` (or `auto`)
  **THEN** the dropdown shows NO Off option (only real effort tiers, or is hidden entirely
  for a non-reasoning model).

- **GIVEN** a carried-over selection of `"off"` and the user switches to a model that
  cannot disable reasoning
  **THEN** the selection clamps to the lowest real tier (never lingers as `"off"`), and the
  persisted `chat-effort` is updated to that tier.

## Background reasoning-off wiring

- **GIVEN** a mechanical background task runs on GLM-5.2 (memory extract/consolidate/forget,
  chat suggestions, context-fold summary, statement row/schema/metadata/vision parse, the
  statement Lucius pass, CSV column mapping, equity-8B parse, account-describe, price-alert
  & intel-digest narration)
  **THEN** it calls the seam with `effort="none"` → the OpenRouter body carries
  `reasoning: {"enabled": false}` → reasoning tokens are not generated or billed.

- **GIVEN** a judgment/synthesis task (analyst alert synthesis, intel asset briefs,
  news-screen materiality, dedup L3 judge)
  **THEN** no `effort` is passed and reasoning stays at the provider default (ON).

## Evidence

- Live two-call OpenRouter verification: default → 20 reasoning tokens; `enabled:false` →
  0 reasoning tokens (completion 26 → 6). Reproduced on `z-ai/glm-5.2`.
- Unit: `tests/test_model_reasoning_capability.py::TestCanDisableReasoning`.
- Frontend unit: `ModelEffortRow.test.tsx` — "offers an Off option…" and "does NOT offer
  Off… (clamps a carried off)".

## Status

- [x] Backend capability + `/api/models` field + unit tests
- [x] Frontend picker (both surfaces) + unit tests
- [x] Background wiring on mechanical tasks
- [ ] `make qa-e2e` for the touched criteria — NOT yet run (needs a running QA instance)
- [ ] `make ui-gate` clean + screenshot evidence — NOT yet captured
