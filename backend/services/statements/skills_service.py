"""Statement-format skills — ORDINARY skills, not a parallel mechanism (owner decision
2026-07-12, superseding the bespoke draft/save tools this module briefly shipped).

A statement skill is a regular SkillStore skill the user (or Lucius, via the normal
``create_skill`` tool) authors like any other: name it, write the guidance in the body —
column meanings, sign conventions, worked examples, quirks — and mark it applicable with
``metadata.statement_format = "true"`` (plus optional ``metadata.account_id`` to scope it to
one account). It shows up in ``list_skills``, the ``/`` picker, and the Skills UI; it's edited
with ``update_skill`` and forgotten with ``delete_skill``. Nothing bespoke.

This module is only the PARSER BRIDGE: resolve the applicable skills for an import and hand
their bodies to the document tiers as a bounded guidance block. Teaching workflow (draft →
verify the example reconciles → save) lives in the managed ``statement-import`` skill's
playbook — the deterministic guards on every import remain the real enforcement."""

from __future__ import annotations

import json
import logging

from backend.core.untrusted import sanitize_untrusted

logger = logging.getLogger(__name__)

_MAX_GUIDANCE_CHARS = 3_500
_MARKER = "statement_format"
# `metadata.match_policy` (RFC 2026-07-29 §D6) — a JSON string, because SkillStore metadata
# values are strings. Bounded before parsing: this is a persistent, AI-writable channel
# (`update_skill` takes no confirmation), so it is untrusted input, not configuration.
_POLICY_KEY = "match_policy"
_MAX_POLICY_JSON = 2_000
_MAX_SKILL_LABEL = 80


def applicable_statement_skills(account_id: str | None) -> list[dict]:
    """Enabled skills marked ``metadata.statement_format`` that apply to this import:
    account-scoped skills for THIS account first, then global (unscoped) ones."""
    try:
        from backend.db.skill_store import SkillStore

        rows = [
            s
            for s in SkillStore().all()
            if s.get("enabled")
            and str((s.get("metadata") or {}).get(_MARKER, "")).lower() in ("true", "1", "yes")
        ]
    except Exception:  # skills are an enhancement — never block a parse
        logger.exception("statement skills: lookup failed")
        return []
    scoped = [s for s in rows if (s.get("metadata") or {}).get("account_id") == (account_id or "")]
    if account_id:
        scoped = [s for s in rows if (s.get("metadata") or {}).get("account_id") == account_id]
    global_ = [s for s in rows if not (s.get("metadata") or {}).get("account_id")]
    ordered = scoped + [s for s in global_ if s not in scoped]
    ordered.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return ordered


def _parse_policy_metadata(raw) -> dict | None:
    """``metadata.match_policy`` → a plain dict, or None. Bounded and fail-quiet: a skill whose
    policy JSON is malformed contributes NOTHING rather than breaking the import that reads it.
    The dict is still untrusted here — ``match_policy.extract_fields`` applies the same
    ``_EXTRACTABLE_FIELDS`` allowlist to it as to the model's own reply (security review #4)."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw[:_MAX_POLICY_JSON])
    except (ValueError, TypeError):
        logger.info("statement skills: ignoring an unparseable metadata.match_policy")
        return None
    return parsed if isinstance(parsed, dict) else None


def match_policy_layers(account_id: str | None) -> list[tuple[str, dict]]:
    """The §D6 skill layers for this import, BROADEST FIRST: global statement skills, then the
    ones scoped to this account. ``resolve_policy`` applies them in order, so an account-scoped
    skill overrides a global one field by field.

    Each layer's label carries the skill's own name for the provenance chips — sanitized and
    capped, since a skill name is user/AI-authored text heading for the wire (#11)."""
    global_layers: list[tuple[str, dict]] = []
    scoped_layers: list[tuple[str, dict]] = []
    for skill in applicable_statement_skills(account_id):
        metadata = skill.get("metadata") or {}
        parsed = _parse_policy_metadata(metadata.get(_POLICY_KEY))
        if parsed is None:
            continue
        name = sanitize_untrusted(
            str(skill.get("name") or skill.get("id") or "skill"), max_len=_MAX_SKILL_LABEL
        )
        if metadata.get("account_id"):
            scoped_layers.append((f"account-skill:{name}", parsed))
        else:
            global_layers.append((f"global-skill:{name}", parsed))
    return global_layers + scoped_layers


def guidance_for(account_id: str | None) -> tuple[str | None, list[str]]:
    """Compose the guidance block the document tiers inject into extraction prompts —
    the applicable skills' own bodies, bounded. Returns ``(guidance, skill_names)``."""
    skills = applicable_statement_skills(account_id)
    if not skills:
        return None, []
    parts: list[str] = []
    names: list[str] = []
    budget = _MAX_GUIDANCE_CHARS
    for skill in skills:
        body = str(skill.get("body") or "").strip()
        if not body:
            continue
        chunk = f"[skill: {skill.get('name')}]\n{body}"
        if len(chunk) > budget:
            chunk = chunk[:budget]
        parts.append(chunk)
        names.append(str(skill.get("name") or skill.get("id")))
        budget -= len(chunk)
        if budget <= 0:
            break
    if not parts:
        return None, []
    return "\n\n".join(parts), names
