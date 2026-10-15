"""skill_creator_bundle — the built-in 'skill-creator' managed skill.

Writes/refreshes the skill-creator skill folder: SKILL.md (body from the starter
definition) + references/*.md (authoring guide, testing & iteration, examples).

In the file-first layout, SKILL.md IS the skill — there's no separate bundle version
or manifest.json divergence. The body comes directly from the STARTER_SKILLS entry,
and the methodology is adapted from Anthropic's skill-creator to this app's tools.

Layering: services → db + services (writes via SkillStore + the skill folder).
Called from startup and from the add-starter REST path — NEVER from db.
"""

from __future__ import annotations

import logging

from backend.db.skill_store import STARTER_SKILLS_DIR

logger = logging.getLogger(__name__)

SKILL_CREATOR_ID = "skill-creator"

# The reference files ship beside the starter's SKILL.md (``backend/starter_skills/``).
_REFERENCES_DIR = STARTER_SKILLS_DIR / SKILL_CREATOR_ID / "references"
SKILL_CREATOR_REFERENCES: dict[str, str] = {
    path.name: path.read_text("utf-8") for path in sorted(_REFERENCES_DIR.glob("*.md"))
}

# Managed content fields refreshed from the starter on boot. Safe: author=builtin →
# read-only in the UI, so a refresh can never clobber user edits.
_MANAGED_FIELDS = ("body", "description")


def ensure_skill_creator_installed() -> bool:
    """Ensure skill-creator EXISTS and carries the CURRENT starter content + reference
    files in one exclusive transaction. Startup catches failures; direct callers see them."""
    from backend.db.skill_store import STARTER_SKILLS, SkillStore, skill_store_lock
    from backend.services.skill_bundles import NotFoundError, read_bundle, write_bundle

    starter = next((s for s in STARTER_SKILLS if s.get("id") == SKILL_CREATOR_ID), None)
    if starter is None:
        raise RuntimeError("managed skill-creator starter is missing")
    store = SkillStore()
    with skill_store_lock(exclusive=True):
        existing = store.find_by_id(SKILL_CREATOR_ID)
        final_enabled = (
            existing["enabled"] if existing is not None else bool(starter.get("enabled", True))
        )

        def _norm(value):
            return value.rstrip("\n") if isinstance(value, str) else value

        refresh = {
            key: starter[key]
            for key in _MANAGED_FIELDS
            if key in starter
            and (existing is None or _norm(existing.get(key)) != _norm(starter[key]))
        }
        try:
            current_files = read_bundle(SKILL_CREATOR_ID, "live")["files"] if existing else {}
        except NotFoundError:
            current_files = {}
        references_changed = any(
            current_files.get(f"references/{name}") != content
            for name, content in SKILL_CREATOR_REFERENCES.items()
        )
        if existing is not None and not refresh and not references_changed:
            return False

        if existing is None:
            store.create({**starter, "author": "builtin", "enabled": False})
            logger.info("seeded the skill-creator meta-skill (disabled until bundle commit)")
        else:
            store.update_skill(SKILL_CREATOR_ID, {**refresh, "enabled": False})
            if refresh:
                logger.info("refreshed the skill-creator meta-skill: %s", sorted(refresh))

        write_bundle(
            SKILL_CREATOR_ID,
            "live",
            skill_md="",
            manifest={"scripts": []},
            references=SKILL_CREATOR_REFERENCES,
        )
        if final_enabled:
            store.update_skill(SKILL_CREATOR_ID, {"enabled": True})
        logger.info("wrote/refreshed skill-creator reference files")
        return True
