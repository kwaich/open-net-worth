"""Pydantic-model-driven structured LLM output — the thin validate+reask layer.

WHY THIS (and not Instructor / Pydantic-AI / BAML)
--------------------------------------------------
The research (docs/adr/2026-07-19-structured-output-layer.md) is clear: hand-writing
JSON Schema is the anti-pattern, and production agents that own their loop roll a thin
"typed model → derived schema → validate → reask" helper over their own provider layer
rather than adopting a framework that would fight that layer. This repo already owns the
whole substrate — ``schema_of(model)`` (Pydantic → JSON schema) + ``core.llm_seam`` (the
provider abstraction). So this module is only the last few dozen lines: derive the schema,
force it through the seam, validate the parsed output with the SAME Pydantic model, and
reask on a ``ValidationError``.

The robustness is in the PROVIDER-INDEPENDENT net (validate + reask), NOT in getting every
provider's native structured-output param right — those are a graceful-fallback optimization
in the provider (``openai_provider`` degrade ladder / ``anthropic_provider`` output_config),
so a provider changing its API can only DEGRADE us to json_object + prompt, never break us.

Callers in ``ai``/``api``/``monitoring`` use ``complete_model``; ``services`` (which must not
import ``ai``) use ``core.llm_seam.complete_json`` directly with a plain schema dict + validator.
"""

from __future__ import annotations

import logging
from typing import Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from backend.ai.tools.schema_from_model import schema_of
from backend.core import llm_seam

logger = logging.getLogger(__name__)

_M = TypeVar("_M", bound=BaseModel)


def _format_errors(exc: ValidationError) -> str:
    """A compact, model-facing rendering of a ValidationError for the reask message."""
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)


async def complete_model(
    prompt: str,
    response_model: Type[_M],
    *,
    system: str = "",
    model: str = "",
    max_tokens: int = 2048,
    source: str = "complete_model",
    effort: str = "",
    max_reasks: int = 1,
) -> Optional[_M]:
    """Structured completion → a VALIDATED ``response_model`` instance, or ``None`` on
    failure (logged by the seam). One source of truth: ``schema_of(response_model)`` derives
    the schema (native constrained decoding where the provider supports it, else json_object
    + a prompt-injected shape) and ``response_model.model_validate`` is the guarantee — a
    ``ValidationError`` drives a guided reask. Never raises into the caller; the caller MUST
    handle ``None`` by SKIPPING (never shipping raw/empty output)."""
    schema = schema_of(response_model, drop_docstrings=True)
    box: dict = {}

    def _validate(obj) -> Optional[str]:
        try:
            box["value"] = response_model.model_validate(obj)
            return None
        except ValidationError as exc:
            return _format_errors(exc)

    result = await llm_seam.complete_json(
        prompt,
        system=system,
        model=model,
        max_tokens=max_tokens,
        source=source,
        effort=effort,
        schema=schema,
        validate=_validate,
        max_reasks=max_reasks,
        # This IS schema_of(response_model) above — the one shape claude_cli_provider's
        # _destrictify_for_cli can safely reverse strictify_schema's widening on (see that
        # function's own docstring, and _response_format_for's pydantic_derived docstring for
        # why a hand-written services/ schema must never set this).
        pydantic_derived=True,
    )
    return box.get("value") if result is not None else None
