"""Ship a built-in starter's bundled files (``assets/``, ``references/``) with the skill.

``SkillStore.create`` writes only SKILL.md, so a starter whose folder carries more — a report
template, say — needs those files written into the installed skill folder too. Installed
copies are kept in step on boot, but ONLY while their body is the current shipped one: an
owner-edited skill is never touched.

``skill-creator`` is excluded — ``skill_creator_bundle`` manages its references itself.
Layering: services → db (the store) + services (bundle writer); never imported by ``db``.
"""

from __future__ import annotations

import logging

from backend.core.errors import NotFoundError
from backend.db.skill_store import STARTER_SKILLS, SkillStore
from backend.services.skill_bundles import read_bundle, write_bundle

logger = logging.getLogger(__name__)

_SELF_MANAGED = {"skill-creator"}
# Files a starter once shipped and no longer does — removed from an unedited install, or the
# skill folder keeps instructions its SKILL.md stopped pointing at.
# (2026-10-06 retired the reports' references/slots.md; 2026-10-07 brought it back with the
# designed page, so nothing is retired today.)
_RETIRED_FILES: dict[str, tuple[str, ...]] = {}


def _starter(starter_id: str) -> dict | None:
    return next((s for s in STARTER_SKILLS if s.get("id") == starter_id), None)


def write_starter_files(starter_id: str) -> bool:
    """Write the starter's bundled files into its installed folder when any differ.

    Returns True when something was written. No-op for an unknown, file-less or
    self-managed starter, an uninstalled one, or one whose body the owner has edited."""
    starter = _starter(starter_id)
    files = (starter or {}).get("files") or {}
    if not files or starter_id in _SELF_MANAGED:
        return False
    installed = SkillStore().find_by_id(starter_id)
    if installed is None or (installed.get("body") or "").strip() != starter["body"].strip():
        return False
    try:
        current = read_bundle(starter_id, "live").get("files") or {}
    except NotFoundError:
        current = {}
    stale = [rel for rel in _RETIRED_FILES.get(starter_id, ()) if rel in current]
    changed = not all(current.get(rel) == content for rel, content in files.items())
    if changed:
        write_bundle(starter_id, "live", skill_md="", manifest={"scripts": []}, extra_files=files)
    if stale:
        _remove_files(starter_id, stale)
    return changed or bool(stale)


def _remove_files(starter_id: str, paths: list[str]) -> None:
    from backend.db.skill_store import (
        pinned_skill_folder,
        remove_pinned_skill_file,
        skill_store_lock,
    )

    with skill_store_lock(exclusive=True), pinned_skill_folder(starter_id) as pinned:
        for rel in paths:
            remove_pinned_skill_file(pinned, rel)
        pinned.verify_identity()


def sync_installed_starter_files() -> list[str]:
    """Boot step: bring every installed, unedited starter's bundled files up to date."""
    synced = [s["id"] for s in STARTER_SKILLS if write_starter_files(s["id"])]
    if synced:
        logger.info("wrote bundled starter files: %s", ", ".join(synced))
    return synced
