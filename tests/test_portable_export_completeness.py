"""Regression test for P0-7: the export manifest must include the user-authored stores
it silently dropped before — equity grants + vest history, custom Agents, and skills
(content + state) — so an export→fresh-instance restore is truly "a single .zip of every
store" (the promise in Settings → Data), not one that quietly loses equity comp and AI config.
"""

from __future__ import annotations

from backend.core.portable import _DICT_ROOT_FILENAMES, PORTABLE_DIRS, PORTABLE_FILES
from backend.paths import (
    AGENTS_JSON,
    GRANTS_JSON,
    SKILLS_DIR,
    SKILLS_STATE_JSON,
    VEST_EVENTS_JSON,
)


def test_equity_and_ai_config_stores_are_in_the_export_manifest() -> None:
    names = {p.name for p in PORTABLE_FILES}
    for path in (GRANTS_JSON, VEST_EVENTS_JSON, AGENTS_JSON, SKILLS_STATE_JSON):
        assert path.name in names, (
            f"{path.name} missing from PORTABLE_FILES — it would be silently dropped on export"
        )


def test_skills_content_dir_is_portable() -> None:
    assert SKILLS_DIR in PORTABLE_DIRS


def test_skills_state_resets_as_a_dict_root() -> None:
    # skills_state.json is {skill_id: {...}} — a keyed dict; a bundle that omits it must
    # reset it to {} on import, not [].
    assert SKILLS_STATE_JSON.name in _DICT_ROOT_FILENAMES
