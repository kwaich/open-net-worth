"""Per-model reasoning capability for the model picker.

`effort_levels` returns the EXACT effort tiers a model accepts, so the UI shows
only real options (owner: don't lie about available efforts, 2026-07-18) and can
clamp a carried-over selection to the nearest supported tier when the user
switches models. `reasoning_supported` is the derived show/hide bool (non-empty
level list).

Source of the level list, per provider:
  - OpenRouter: AUTHORITATIVE live metadata. Each model in GET /api/v1/models may
    carry a ``reasoning`` object whose ``supported_efforts`` lists the exact accepted
    tiers (OpenRouter values minimal/none are dropped — we don't expose them). We
    intersect that with our ladder, in ladder order. A reasoning model with no
    explicit list (``supported_efforts: null`` = all accepted) or a fallback row with
    no metadata → full ladder; a non-reasoning model (no ``reasoning`` object, no
    "reasoning" in supported_parameters) → none.
  - api.openai.com / api.x.ai: the caller cross-references OpenRouter's PUBLIC catalog
    (``x-ai/<id>`` / ``openai/<id>``) and passes the found efforts as ``cross_ref`` —
    authoritative when present, so a new model self-describes. ``cross_ref`` is None
    when the model isn't in OpenRouter's catalog, and we fall back to the CURATED
    tables below (which mirror what the provider actually accepts):
      * api.openai.com: low/medium/high for reasoning families (o1/o3/o4/gpt-5); else none.
      * api.x.ai: grok-4.5/grok-4.20 → low/medium/high; grok-3-mini → low/high; else none.
  - Anthropic: low/medium/high/max, blanket (every current Claude reasons; OpenRouter's
    Claude effort mapping differs from the native API, so we deliberately DON'T cross-ref).
  - local / unknown OpenAI-compatible: full ladder (don't hide a possibly-working
    dial we can't positively rule out).
"""

from __future__ import annotations

from typing import Optional, Sequence

from .openai_provider import OpenAIProvider

# The app's full effort ladder, low → max.
FULL_LADDER: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


def _openrouter_levels(
    supported_parameters: Optional[Sequence[str]], reasoning_meta: Optional[dict]
) -> list[str]:
    """OpenRouter's accepted tiers from its live per-model metadata.

    Prefers the ``reasoning.supported_efforts`` list (authoritative), intersected
    with our ladder in ladder order; ``null``/missing efforts on a reasoning model →
    full ladder. Falls back to the ``supported_parameters`` "reasoning" signal, then
    to full ladder when there's no metadata at all (a fallback row) so we never hide.
    """
    if reasoning_meta is not None:
        efforts = reasoning_meta.get("supported_efforts")
        if isinstance(efforts, list) and efforts:
            filtered = [e for e in FULL_LADDER if e in efforts]
            return filtered or list(FULL_LADDER)
        return list(FULL_LADDER)  # reasoning model, granularity unspecified → all
    if supported_parameters is None:
        return list(FULL_LADDER)  # no metadata at all (fallback) — don't hide
    return list(FULL_LADDER) if "reasoning" in supported_parameters else []


def _ladder_intersect(efforts: Sequence[str]) -> list[str]:
    """Keep only our ladder tiers, in ladder order (drops OpenRouter minimal/none)."""
    return [e for e in FULL_LADDER if e in efforts]


def effort_levels(
    transport: str,
    base_url: str,
    model_id: str,
    supported_parameters: Optional[Sequence[str]] = None,
    reasoning_meta: Optional[dict] = None,
    cross_ref: Optional[Sequence[str]] = None,
) -> list[str]:
    """The effort tiers this (transport, model) actually accepts, in ladder order.

    Empty list → the effort dial does nothing for this model (hide it).
    ``reasoning_meta`` is OpenRouter's per-model ``reasoning`` object when available.
    ``cross_ref`` is the efforts found for a DIRECT OpenAI/xAI model by looking it up
    in OpenRouter's public catalog (authoritative when not None — an empty list means
    the catalog knows it and it's non-reasoning; None means not found → curated).
    """
    if transport == "anthropic":
        from .anthropic_provider import _ADAPTIVE_NO_XHIGH_PREFIXES, _XHIGH_CAPABLE_PREFIXES

        mid = (model_id or "").lower()
        if mid.startswith(_XHIGH_CAPABLE_PREFIXES):
            return ["low", "medium", "high", "xhigh", "max"]  # native "Extra" = xhigh
        if mid.startswith(_ADAPTIVE_NO_XHIGH_PREFIXES):
            return ["low", "medium", "high", "max"]  # Opus 4.6 / Sonnet 4.6: no xhigh
        # Legacy budget-token models (Haiku 4.5, Sonnet 4.5, Opus 4.5, older): the budget
        # map distinguishes high/xhigh/max, low/medium both fold to no-thinking.
        return ["low", "high", "xhigh", "max"]

    if transport == "claude_cli":
        # The headless CLI takes --effort low|medium|high|xhigh|max for every model it
        # serves and resolves the per-model ladder itself, so the full ladder is honest
        # here. base_url is the CLI PATH on this transport, never an endpoint — the
        # base_url sniffing below must not see it.
        return ["low", "medium", "high", "xhigh", "max"]

    if transport == "codex_cli":
        # The CLI publishes each model's real ladder in its own models cache, and the rungs
        # differ per model (GPT-5.6-Sol reaches xhigh/max; older ones stop at high). Ask it
        # rather than asserting one ladder for every model — the flat low|medium|high we
        # used to return understated every current model. Unknown model → that floor.
        # base_url is the CLI PATH on this transport, never an endpoint, so the base_url
        # sniffing below must not see it.
        from backend.ai.models.codex_cli_catalog import effort_levels_for

        return effort_levels_for(model_id) or ["low", "medium", "high"]

    if transport == "antigravity_cli":
        # The Antigravity CLI takes --effort low|medium|high.
        return ["low", "medium", "high"]

    base = (base_url or "").lower()
    lc = (model_id or "").lower()

    if "openrouter.ai" in base:
        return _openrouter_levels(supported_parameters, reasoning_meta)
    if "api.openai.com" in base:
        if cross_ref is not None:
            return _ladder_intersect(cross_ref)
        return (
            ["low", "medium", "high"]
            if lc.startswith(OpenAIProvider._OPENAI_REASONING_PREFIXES)
            else []
        )
    if "api.x.ai" in base:
        if cross_ref is not None:
            return _ladder_intersect(cross_ref)
        if lc.startswith(OpenAIProvider._XAI_LMH_PREFIXES):
            return ["low", "medium", "high"]
        if lc.startswith(OpenAIProvider._XAI_LOWHIGH_PREFIXES):
            return ["low", "high"]
        return []
    return list(FULL_LADDER)


def reasoning_supported(
    transport: str,
    base_url: str,
    model_id: str,
    supported_parameters: Optional[Sequence[str]] = None,
    reasoning_meta: Optional[dict] = None,
    cross_ref: Optional[Sequence[str]] = None,
) -> bool:
    """Whether the effort dial has any effect (i.e. any level is accepted)."""
    return bool(
        effort_levels(
            transport, base_url, model_id, supported_parameters, reasoning_meta, cross_ref
        )
    )


def can_disable_reasoning(
    transport: str,
    base_url: str,
    model_id: str,
    supported_parameters: Optional[Sequence[str]] = None,
    reasoning_meta: Optional[dict] = None,
    cross_ref: Optional[Sequence[str]] = None,
) -> bool:
    """Whether ``effort="off"`` genuinely turns reasoning OFF for this model — the signal
    the picker uses to show an honest "Off" option next to the effort tiers.

    ONLY OpenRouter reasoning models qualify today, and only because it was verified end to
    end: OpenRouter's unified ``reasoning: {"enabled": false}`` disables the underlying model's
    thinking (proven live against GLM-5.2 — 20 reasoning tokens → 0). We DON'T claim "Off"
    where our code can't honestly deliver it:
      - api.openai.com reasoning families map ``none`` → ``reasoning_effort: "minimal"`` — a
        near-off floor, NOT a true off, so calling it "Off" would lie;
      - Anthropic coerces an unknown effort to ``medium`` (``_thinking_kwargs``) — no clean off;
      - api.x.ai / local / unknown OpenAI-compatible send NOTHING for ``none`` — a no-op.
    Note OpenRouter's own ``supported_efforts`` is NOT a reliable disable signal (GLM-5.2
    lists only high/xhigh yet ``enabled: false`` works), so we key on reasoning-support +
    OpenRouter transport, not on a ``"none"`` rung being advertised.
    """
    if "openrouter.ai" not in (base_url or "").lower():
        return False
    return reasoning_supported(
        transport, base_url, model_id, supported_parameters, reasoning_meta, cross_ref
    )
