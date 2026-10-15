"""Provider-neutral lifecycle helpers for host-managed inline skill invocations."""

from __future__ import annotations

import hashlib
import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from backend.ai.models import price_map
from backend.db.skill_files import allows_implicit_invocation, allows_model_invocation
from backend.db.skill_store import SkillStore
from backend.services import skills as skills_service

ACTIVATION_MARKER = "_onw_skill_activation"
DEACTIVATION_MARKER = "_onw_skill_deactivation"
MAX_CATALOG_DESCRIPTION_CHARS = 1_024
logger = logging.getLogger(__name__)

_CATALOG_HEADER = (
    "YOUR SKILLS — metadata only. If the user names a skill or the task clearly matches a "
    "description, call activate_skill with its id before acting. Multiple distinct skills may "
    "be activated in one turn. Once invoked, the rendered instructions remain conversation "
    "context for the rest of this session. There is no conversational skill-deactivation "
    "operation; begin a new conversation when the user wants a clean context. An ACTIVE SKILL "
    "CONTEXT is host-restored continuity, not pasted user text or a new invocation."
)


@dataclass(frozen=True)
class InvocationRequest:
    """One resolved skill invocation with optional Claude-compatible arguments."""

    skill_id: str
    source: Literal["user", "model"] = "user"
    argument_text: str = ""

    def __post_init__(self) -> None:
        if self.source not in {"user", "model"}:
            raise ValueError("skill invocation source must be 'user' or 'model'")


def invocation_entry(
    request: InvocationRequest,
    skill: Mapping[str, Any],
    rendered: str,
) -> dict:
    """Build one persisted user-role invocation message with server-private metadata."""
    skill_id = str(skill.get("id") or skill.get("name") or "")
    skill_name = str(skill.get("name") or skill_id)
    content_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    content = f"[SKILL INVOCATION: {skill_name} ({skill_id})]\n{rendered}"
    return {
        "role": "user",
        "content": content,
        "_onw_skill": {
            "skill_id": skill_id,
            "skill_name": skill_name,
            "content_hash": content_hash,
            "full": True,
            "source": request.source,
            # Claude Code-compatible invocation text is part of the durable host receipt.
            # It is re-rendered from the current SKILL.md on later turns rather than
            # trusting an old instruction body from transcript history.
            "argument_text": request.argument_text,
        },
    }


def deactivation_entry(skill_id: str) -> dict:
    """Build one durable, provider-hidden conversation-scope deactivation receipt."""
    normalized = str(skill_id or "").strip()
    if not normalized:
        raise ValueError("skill_id is required")
    return {
        "role": "user",
        "content": "",
        DEACTIVATION_MARKER: {"skill_id": normalized},
    }


def active_invocations(history: Sequence[dict]) -> list[InvocationRequest]:
    """Resolve the conversation's ordered active-skill state from durable receipts.

    Every successfully invoked skill is sticky, matching Claude Code's re-injection
    behavior regardless of whether the user or model selected it. Re-invoking an active
    skill replaces its arguments/source and moves it to the newest position; a deactivation
    receipt removes it without rewriting history.
    """
    active: dict[str, InvocationRequest] = {}
    for entry in history:
        marker = entry.get("_onw_skill")
        source = marker.get("source") if isinstance(marker, Mapping) else None
        if isinstance(marker, Mapping) and source in {"user", "model"}:
            skill_id = str(marker.get("skill_id") or "").strip()
            if not skill_id:
                continue
            active.pop(skill_id, None)
            active[skill_id] = InvocationRequest(
                skill_id=skill_id,
                source=source,
                argument_text=str(marker.get("argument_text") or ""),
            )
            continue
        removal = entry.get(DEACTIVATION_MARKER)
        if isinstance(removal, Mapping):
            active.pop(str(removal.get("skill_id") or "").strip(), None)
    return list(active.values())


def prepare_invocation(
    request: InvocationRequest,
    history: Sequence[dict],
    *,
    skill: Mapping[str, Any],
) -> dict:
    """Load the canonical skill file for this invocation."""
    del history
    return invocation_entry(
        request,
        skill,
        skills_service.render(
            dict(skill),
            argument_text=request.argument_text,
            argument_source=request.source,
        ),
    )


def activation_from_tool_result(result: str | Mapping[str, Any]) -> InvocationRequest | None:
    """Decode a validated server-private activation marker from a tool result."""
    payload: Any = result
    if isinstance(result, str):
        try:
            payload = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            return None
    if not isinstance(payload, Mapping):
        return None
    activation = payload.get(ACTIVATION_MARKER)
    if not isinstance(activation, Mapping) or not isinstance(activation.get("skill_id"), str):
        return None
    source = activation.get("source")
    if source not in {"user", "model"}:
        return None
    return InvocationRequest(
        skill_id=str(activation["skill_id"]),
        source=source,
        argument_text=(
            str(activation.get("argument_text") or "")
            if isinstance(activation.get("argument_text"), str)
            else ""
        ),
    )


def _content_blocks(content: str | list) -> list:
    if isinstance(content, list):
        return deepcopy(content)
    return [{"type": "text", "text": content}]


# The ONLY keys a provider may receive. An ALLOWLIST, not a denylist, because this is the
# last hop before the wire and the transcript is about to start carrying render metadata
# (`ts`, `usage`, …) so a reloaded conversation matches the live one. `_prepare_messages`
# forwards entries verbatim (`result.append(msg)`) and copies with `{**msg, ...}`, so any
# key added anywhere upstream would otherwise ride into the request — and an unexpected
# field on a message is exactly the shape that produces a `400`. Popping known host keys
# one at a time (this function's previous `pop("_onw_skill")`) makes correctness a function
# of remembering; the allowlist makes a new host-only field safe the day it is added.
_PROVIDER_MESSAGE_KEYS = ("role", "content")


def provider_messages(messages: Sequence[dict]) -> list[dict]:
    """Project host transcript entries into provider-valid messages."""
    projected: list[dict] = []
    for source in messages:
        message = {k: deepcopy(v) for k, v in source.items() if k in _PROVIDER_MESSAGE_KEYS}
        if projected and message.get("role") == "user" == projected[-1].get("role"):
            prior = projected[-1]
            left = prior.get("content")
            right = message.get("content")
            if isinstance(left, str) and isinstance(right, str):
                prior["content"] = f"{left}\n\n{right}"
            elif isinstance(left, (str, list)) and isinstance(right, (str, list)):
                prior["content"] = _content_blocks(left) + _content_blocks(right)
            else:
                projected.append(message)
        else:
            projected.append(message)
    return projected


def record_usage(skill_id: str) -> None:
    """Persist one validated invocation through the skill repository."""
    SkillStore().increment_invocation_count(skill_id)


def usage_counts() -> dict[str, int]:
    """Return persisted invocation counts for catalog ranking."""
    return SkillStore().invocation_counts()


def catalog_budget_chars(model: str, base_url: str = "") -> int:
    """Codex's 2% metadata budget, using the four-characters-per-token estimate."""
    context_tokens = price_map.max_input_tokens(model, base_url)
    return int(context_tokens * 0.02 * 4) if context_tokens > 0 else 8_000


def _catalog_trigger(skill: Mapping[str, Any]) -> str:
    claude = skill.get("claude")
    when_to_use = str(claude.get("when_to_use") or "") if isinstance(claude, Mapping) else ""
    trigger = " ".join(f"{skill.get('description') or ''} {when_to_use}".split())
    if len(trigger) <= MAX_CATALOG_DESCRIPTION_CHARS:
        return trigger
    return trigger[: MAX_CATALOG_DESCRIPTION_CHARS - 3] + "..."


def _catalog_record(skill: Mapping[str, Any], trigger: str) -> str:
    skill_id = str(skill.get("id") or "").strip()
    name = str(skill.get("name") or "").strip()
    safe_trigger = trigger.replace("\n", " ").replace("<", "").replace(">", "")
    return (
        f"- {name}: {safe_trigger} (skill id: {skill_id})"
        if safe_trigger
        else (f"- {name}: (skill id: {skill_id})")
    )


def catalog_text(
    skills: Sequence[Mapping[str, Any]],
    *,
    model: str = "",
    base_url: str = "",
    counts: Mapping[str, int] | None = None,
) -> str:
    """Render Codex-style progressive-disclosure metadata under the 2% budget."""
    eligible = sorted(
        [
            skill
            for skill in skills
            if skill.get("enabled", True)
            and allows_implicit_invocation(skill.get("openai"))
            and allows_model_invocation(skill.get("claude"))
            and str(skill.get("name") or "").strip()
            and str(skill.get("id") or "").strip()
        ],
        key=lambda skill: (str(skill.get("name") or ""), str(skill.get("id") or "")),
    )
    if not eligible:
        return ""

    del counts
    triggers = {str(skill["id"]): _catalog_trigger(skill) for skill in eligible}
    budget = catalog_budget_chars(model, base_url)

    minimum_lines = [_catalog_record(skill, "") for skill in eligible]
    full_lines = [_catalog_record(skill, triggers[str(skill["id"])]) for skill in eligible]
    minimum_cost = sum(len(line) + 1 for line in minimum_lines)
    full_cost = sum(len(line) + 1 for line in full_lines)
    allocations = {str(skill["id"]): 0 for skill in eligible}
    included = list(eligible)

    if full_cost <= budget:
        allocations = {str(skill["id"]): len(triggers[str(skill["id"])]) for skill in eligible}
    elif minimum_cost <= budget:
        remaining = budget - minimum_cost
        unfinished = {str(skill["id"]) for skill in eligible if triggers[str(skill["id"])]}
        # Codex distributes the remaining description budget round-robin. The first
        # character also costs the separator before the locator; later characters cost one.
        while unfinished:
            progressed = False
            for skill in eligible:
                skill_id = str(skill["id"])
                if skill_id not in unfinished:
                    continue
                current = allocations[skill_id]
                delta = 2 if current == 0 else 1
                if delta <= remaining:
                    allocations[skill_id] = current + 1
                    remaining -= delta
                    progressed = True
                    if allocations[skill_id] >= len(triggers[skill_id]):
                        unfinished.discard(skill_id)
            if not progressed:
                break
    else:
        included = []
        used = 0
        for skill, line in zip(eligible, minimum_lines):
            cost = len(line) + 1
            if used + cost <= budget:
                included.append(skill)
                used += cost

    omitted = len(eligible) - len(included)
    if omitted:
        logger.warning(
            "Exceeded skills context budget; %d skill(s) were omitted from the model-visible catalog.",
            omitted,
        )
    eligible = included
    if not eligible:
        return ""

    for skill in eligible:
        skill_id = str(skill["id"])
        trigger = triggers[skill_id]
        allocated = allocations[skill_id]
        triggers[skill_id] = trigger[:allocated]

    def _render() -> str:
        lines = ["", _CATALOG_HEADER, "### Available skills"]
        for skill in eligible:
            skill_id = str(skill["id"])
            lines.append(_catalog_record(skill, triggers[skill_id]))
        return "\n".join(lines)

    return _render()
