"""Does a model id LOOK like it accepts image input? — a stdlib-only leaf.

Split out of ``ai/models/capabilities.py`` (and kept out of ``services/``) so every layer
that needs the question can ask it: the statement parser (services) must refuse a scanned
PDF up front, the chat path (api) must decide before building an image block, and the
OpenAI-compatible provider (ai) must strip images before the wire call. ``capabilities.py``
can't be that home — it imports ``openai_provider`` for its effort prefixes, which would
drag the whole ``ai`` layer into ``services``.

Everything here is a pure function of a string plus, optionally, the provider's own
declared modality list. No I/O, no stores. The STORED half of the ladder (the owner's
override, a learned refusal, a recorded declaration) lives in
``services/vision_capability.py``.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

# Name patterns that POSITIVELY mark a vision model. Segment/word-anchored, so a family
# name can't accidentally match inside a longer token.
_VISION_NAME_PATTERNS: tuple[str, ...] = (
    r"\bgpt-4o\b",
    r"\bgpt-4\.1\b",
    r"\bgpt-[5-9]",
    r"\bo[134]\b",
    r"\bclaude-(?:3|4|5|sonnet|opus|haiku)\b",
    r"\bgemini\b",
    r"\bgrok-(?:2-vision|[4-9])",
    r"\bqwen[\w.]*-?vl\b",
    r"\b(?:vision|llava|pixtral|internvl|mllama|minicpm-v|moondream)\b",
    r"\bglm-[\d.]*v\b",  # glm-4v / glm-4.1v — the VISION line, not glm-4.6
    r"(?:^|[-_/])vl(?:[-_/]|$)",
)

# Name patterns that positively mark a TEXT-ONLY model. Deliberately narrow: only families
# we have actually confirmed have no vision line under that exact name. GLM is the
# motivating case (glm-4.6 / glm-5.2 are text-only; glm-4v is not, and is caught above
# first, because the vision patterns are checked first).
_TEXT_ONLY_NAME_PATTERNS: tuple[str, ...] = (
    r"\bglm-[\d.]+(?:-air|-flash|-plus)?\b",
    r"\bdeepseek\b",
    r"\bkimi\b",
    r"\bmoonshot\b",
    r"\b(?:codestral|devstral|codellama)\b",
    r"\bqwq\b",
    r"\bo3-mini\b",
    r"\btext-embedding",
)


def vision_from_modalities(input_modalities: Optional[Sequence[str]]) -> Optional[bool]:
    """AUTHORITATIVE read of a provider's own declared input modalities.

    OpenRouter publishes ``architecture.input_modalities`` per model; nothing else we talk
    to does (xAI/OpenAI-direct/GLM/local ``/models`` return bare ``{id}`` rows). ``None``
    when the provider published nothing — never guess from an absent field.
    """
    if input_modalities is None:
        return None
    return "image" in {str(m).strip().lower() for m in input_modalities}


def infer_vision_from_name(model_id: str) -> Optional[bool]:
    """Best-effort vision guess from the model id alone: True / False / None (unknown).

    A HEURISTIC over naming conventions, not a catalog — so it returns ``None`` freely.
    Vision patterns are checked FIRST, because a vision variant's name is usually its text
    sibling's name plus a marker (``glm-4v`` vs ``glm-4.6``) and the text-only pattern would
    otherwise swallow it.
    """
    lc = (model_id or "").strip().lower()
    if not lc:
        return None
    if any(re.search(p, lc) for p in _VISION_NAME_PATTERNS):
        return True
    if any(re.search(p, lc) for p in _TEXT_ONLY_NAME_PATTERNS):
        return False
    return None
