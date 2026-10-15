# Acceptance — Effort dial: reasoning-only, per-model levels, honest + clamped

Owner decision 2026-07-18. The chat effort dial now controls ONLY the model's
reasoning budget — it no longer shapes answer length (the model decides). It is a
DROPDOWN that shows EXACTLY the effort tiers the selected model accepts (no lying),
is HIDDEN when the model accepts none, and CLAMPS a carried-over selection to the
nearest supported tier when the user switches models.

Backend: `_resolve_style` (assistant.py); `GET /api/models` `reasoning` +
`effort_levels` per model; `ai/models/capabilities.py` (`effort_levels`). Frontend:
`effortLevelsFor` / `clampEffort` / `modelSupportsReasoning`, ChatPanel +
ModelEffortRow (both render a dropdown of the model's tiers).

Per-model tiers:
- Anthropic → low/medium/high/max (blanket; NOT cross-referenced — OpenRouter's Claude
  effort mapping differs from the native API)
- OpenRouter reasoning model → its live `reasoning.supported_efforts` (authoritative);
  non-reasoning → none
- Direct OpenAI / xAI → cross-referenced against OpenRouter's PUBLIC catalog by slug
  (`openai/<id>`, `x-ai/<id>`): the found efforts are authoritative, so a new model
  (e.g. grok-4.3) self-describes; a model absent from the catalog falls back to the
  curated table (OpenAI o1/o3/o4/gpt-5 → low/medium/high; xAI grok-4.5/4.20 →
  low/medium/high, grok-3-mini → low/high). The catalog is fetched once per 6h
  (no-auth public endpoint), only when a direct OpenAI/xAI list is priced, and a fetch
  failure silently falls back to curated.
- local / unknown → full ladder

## EF-7 Direct provider self-describes via OpenRouter catalog
GIVEN a direct xAI model not in our curated list (e.g. grok-4.3) that OpenRouter's
public catalog lists with supported_efforts
WHEN GET /api/models prices it
THEN its effort_levels come from the OpenRouter catalog (not hidden, not curated-guessed),
AND if the catalog is unreachable it falls back to the curated table without error.

## EF-1 Effort no longer controls answer length
GIVEN any effort tier selected
WHEN I ask a substantive question
THEN no "RESPONSE STYLE" instruction is injected for that turn (the model calibrates
length itself); only the reasoning API param reflects the tier.

## EF-2 Greeting guard survives
GIVEN any effort tier
WHEN I send a bare "hello" / "thanks"
THEN the smalltalk override still applies (reply stays 1-2 plain sentences) — the
one remaining response-style nudge, independent of the effort dial.

## EF-3 Dial hidden for non-reasoning models
GIVEN the selected model has no reasoning knob (e.g. gpt-4o on OpenAI, grok-4, or an
OpenRouter model whose supported_parameters lacks "reasoning")
WHEN I open the chat composer (dock or side panel)
THEN the effort control is NOT rendered.

## EF-4 Dropdown shows exactly the model's tiers (no lying)
GIVEN grok-3-mini is selected (accepts low/high only)
WHEN I open the effort dropdown
THEN it lists ONLY "low" and "high" — not medium/xhigh/max.
AND GIVEN grok-4.5 (low/medium/high) the dropdown lists low/medium/high.
AND GIVEN a Claude model the dropdown lists low/medium/high/max.

## EF-4b Clamp on model switch
GIVEN effort is "xhigh" and I switch to a model that only accepts low/medium/high
WHEN the composer re-renders
THEN the shown effort becomes "high" (nearest supported ≤ current), the stored
'chat-effort' is updated to "high", and the send path uses "high".
AND switching to a low/high-only model with "medium" selected clamps to "low".

## EF-5 Auto + loading never hide the dial
GIVEN model = "auto", OR the model list has not loaded yet
WHEN the composer renders
THEN the effort control is shown (target unknown → don't hide; no flicker on slow
fetch).

## EF-6 Unknown/local models default to shown
GIVEN a local (Ollama) or otherwise unclassifiable model
WHEN the composer renders
THEN the effort control is shown (we never hide a control we can't positively rule
out as inert).
