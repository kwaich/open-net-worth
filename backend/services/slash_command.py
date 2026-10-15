"""Resolve structured or leading slash-command skills for host-owned invocation.

The chat route passes immutable skill ids plus Claude-compatible trailing argument text
to the assistant. The original user message remains ordinary model input as well.

Dependency rule: services layer — may import db and core, must NOT import ai.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from backend.core.errors import ValidationError

# Pattern: message starts with "/" followed by a valid slug (lowercase, digits, hyphens),
# optionally followed by whitespace and additional text.
_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_LEADING_SLASH_RE = re.compile(rf"^/({_SLUG})(?:\s+|$)", re.IGNORECASE)
_STRUCTURED_SKILL_TOKEN_RE = re.compile(rf"\[skill:\s*({_SLUG})(?:\|[^\]]*)?\]", re.IGNORECASE)
_MAX_STACKED_SKILLS = 6


@dataclass(frozen=True)
class DirectSkillResolution:
    """Host-owned direct skill request, ready for the runtime to render before model use."""

    skill_ids: tuple[str, ...]
    argument_text: str = ""


def _structured_argument_text(text: str, selected_skill_ids: Sequence[str]) -> str:
    """Remove UI/command selection markers while preserving the user's task text."""
    selected = {skill_id.lower() for skill_id in selected_skill_ids}

    def strip_token(match: re.Match[str]) -> str:
        return "" if match.group(1).lower() in selected else match.group(0)

    remaining = _STRUCTURED_SKILL_TOKEN_RE.sub(strip_token, text).strip()
    while True:
        match = _LEADING_SLASH_RE.match(remaining)
        if match is None or match.group(1).lower() not in selected:
            break
        remaining = remaining[match.end() :].strip()
    return re.sub(r"[ \t]+", " ", remaining).strip()


def resolve_slash_skills(
    text: str, structured_skills: Sequence[str] = ()
) -> DirectSkillResolution | None:
    """Resolve ordered structured skills or leading user-invocable ``/skills``.

    Direct text commands deliberately fall back to normal text when any requested skill is
    unknown, disabled, or model-only.  A structured request is an explicit UI selection, so its
    invalid state is a validation error the API can surface rather than silently discarding it.
    """
    from backend.db.skill_files import allows_user_invocation
    from backend.db.skill_store import SkillStore, validate_skill_id

    store = SkillStore()
    selected_structured: list[str] = []
    for raw_skill_id in structured_skills:
        skill_id = validate_skill_id(str(raw_skill_id))
        if skill_id in selected_structured:
            continue
        if len(selected_structured) >= _MAX_STACKED_SKILLS:
            raise ValidationError(
                f"At most {_MAX_STACKED_SKILLS} skills can be selected in one message."
            )
        skill = store.find_by_id(skill_id)
        if skill is None:
            raise ValidationError(f"Unknown structured skill '{raw_skill_id}'.")
        if not skill.get("enabled", True):
            raise ValidationError(f"Structured skill '{skill_id}' is disabled.")
        if not allows_user_invocation(skill.get("claude")):
            raise ValidationError(f"Structured skill '{skill_id}' is model-only.")
        selected_structured.append(str(skill["id"]))
    if selected_structured:
        return DirectSkillResolution(
            tuple(selected_structured), _structured_argument_text(text, selected_structured)
        )

    remaining = text.strip()
    selected: list[str] = []
    while len(selected) < _MAX_STACKED_SKILLS:
        match = _LEADING_SLASH_RE.match(remaining)
        if match is None:
            break
        skill = store.find_by_id(match.group(1).lower())
        if (
            skill is None
            or not skill.get("enabled", True)
            or not allows_user_invocation(skill.get("claude"))
        ):
            if not selected:
                return None
            break
        resolved_id = str(skill["id"])
        if resolved_id not in selected:
            selected.append(resolved_id)
        remaining = remaining[match.end() :].strip()

    if not selected:
        return None
    return DirectSkillResolution(tuple(selected), remaining)
