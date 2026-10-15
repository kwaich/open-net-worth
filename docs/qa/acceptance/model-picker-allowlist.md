# Acceptance — Model picker: capability filter + user preferred-models allowlist

Decision 2026-07-18 (ADR `2026-07-18-model-picker-capability-filter-and-user-allowlist`),
NARROWED 2026-08-06 (ADR `2026-08-06-auto-model-switching-removed` — the routing engine is
deleted, so the shortlist now orders pickers and selects nothing). Two changes, one list:
1. The model picker drops provider models that DECLARE they can't call tools or can't
   output text (OpenRouter's `supported_parameters` + `architecture.output_modalities`);
   models with no such metadata are always kept (unknown → trust).
2. A user **preferred-models shortlist** (`PreferredModelsStore`) pins a "Preferred" group
   atop every picker (empty = the full capability-filtered list). It is display ordering
   only — it never picks the model that runs a turn.

Backend: `api/models._model_capability_ok` + `_preferred_ref_set`/`_mark_preferred`;
`PreferredModelsStore`; `GET/PUT /api/preferred-models`. Frontend:
`ModelOption.preferred`, `buildModelSelectOptions` + ChatPanel "Preferred" group,
Settings → AI Models → **Preferred models** panel.

## MP-1 Capability filter drops non-tool / non-text models
GIVEN an OpenRouter provider whose `/models` returns a tool-capable text model, an
image-output model (no `"tools"`), and an audio-only model (`output_modalities` has no
`"text"`)
WHEN `GET /api/models` is fetched
THEN only the tool-capable text model is listed; the image and audio models are absent.

## MP-2 Metadata-less providers keep text models, drop obvious media by name
GIVEN a provider whose `/models` entries carry no `supported_parameters` /
`architecture` (xAI, OpenAI-direct, GLM, local)
WHEN `GET /api/models` is fetched
THEN plain text ids are kept in order (e.g. `grok-4.5`, `gpt-4o`), AND obvious
non-text media ids are dropped by a modality-NAME heuristic (`grok-imagine-image`,
`grok-imagine-video-1.5`, `dall-e-3`, `tts-1`, `whisper`, `text-embedding-*`) — a new
TEXT model never carries those tokens, so it is not a name allow/deny list.

## MP-3 Preferred flag reflects the allowlist
GIVEN `model_allowlist = ["openrouter:anthropic/claude-sonnet-5"]`
WHEN `GET /api/models` is fetched
THEN that row has `preferred: true` and every other row `preferred: false`
AND a `"provider:model"` ref matches a single-provider row by its bare-id tail.

## MP-4 Preferred group renders atop the picker, full list intact
GIVEN two models flagged `preferred` across two providers
WHEN the chat model dropdown (or a Settings model `<Select>`) renders
THEN a "Preferred" optgroup appears directly after "Default", listing exactly those two
models, AND each still appears in its own provider optgroup below (nothing removed).
AND GIVEN nothing is flagged, no "Preferred" group is rendered.

## MP-5 The shortlist selects nothing (superseded criteria)
MP-5, MP-6 and MP-7's routing halves are RETIRED with the routing engine (ADR
`2026-08-06-auto-model-switching-removed`). What replaces them:
GIVEN any non-empty shortlist
WHEN a chat turn runs
THEN the model that answers is the conversation's explicit pick, or the instance default
when it has none — the shortlist NEVER influences which model runs.

## MP-6 Empty shortlist = full picker
GIVEN the shortlist is empty
WHEN the picker renders
THEN it shows the full capability-filtered list with no "Preferred" group.

## MP-7 Settings → Preferred models UI round-trips
GIVEN Settings → AI Models → the "Preferred models" panel
WHEN I search the combo, add two models as chips, and click "Save preferred models"
THEN a `PUT /api/preferred-models` fires with `models` = the two `provider:model` refs
(saved order preserved), a toast confirms, and a reload shows the same two chips.
AND the Save button is disabled until the selection differs from what's saved.
AND the chips reflect the SAVED list once the fetch lands — not an empty list frozen at
mount.

## Empty / loading / error states
- Nothing picked yet → "Nothing picked — every available model shows in the picker."
- A search that matches nothing → "No unpicked model matches '<query>'." (the popover is
  empty, not broken).
- A failed `PUT` → a "Could not save (HTTP nnn)" toast and the selection is retained for a
  retry.
