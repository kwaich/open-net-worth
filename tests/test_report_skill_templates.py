"""The report skills fill a shipped HTML template, and that template reaches the installed skill.

Before 2026-10-01 the skills told the model to write the report HTML from scratch, so every
run looked different and none looked designed. Now ``assets/report.html`` ships in the skill
folder and the boot sync / install path writes it into the installed copy.
"""

from __future__ import annotations

import re

import pytest

import backend.db.skill_store as skill_store
import backend.services.skill_bundles as skill_bundles
from backend.db.skill_store import STARTER_SKILLS, SkillStore
from backend.services import starter_skill_files

_REPORTS = ("weekly-report", "monthly-report")
_SLOT = re.compile(r"\{\{(\w+)\}\}")


def _starter(sid: str) -> dict:
    return next(s for s in STARTER_SKILLS if s["id"] == sid)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    monkeypatch.setattr(skill_bundles, "SKILLS_DIR", skills_dir)
    return skills_dir


@pytest.mark.parametrize("sid", _REPORTS)
def test_every_template_slot_is_documented_and_every_marker_balances(sid):
    """The designed page is fixed; Lucius fills it and may add extra sections from the kit
    (owner, 2026-10-07: a free page came out "ugly ... too much numbers, text")."""
    from backend.services.artifact_density import MARK
    from backend.services.artifact_design import STYLESHEET

    starter = _starter(sid)
    template = starter["files"]["assets/report.html"]
    body = starter["body"]
    slots_doc = starter["files"]["references/slots.md"]
    kit = starter["files"]["references/components.md"]
    # Progressive disclosure: read only when filling the page / building an extra section.
    for ref in ("`assets/report.html`", "`references/slots.md`", "`references/components.md`"):
        assert ref in body
    assert MARK in template  # the server's density limits apply to this page
    for slot in set(_SLOT.findall(template)):
        assert f"`{slot}`" in slots_doc, f"{sid}: slot {slot} is not documented in slots.md"
    for kind in ("repeat", "optional"):
        opens = re.findall(rf"<!-- {kind}: [\w-]+ -->", template)
        assert len(opens) == template.count(f"<!-- /{kind} -->"), f"{sid}: unbalanced {kind}"
    for cls in set(re.findall(r'class="([^"]+)"', kit)):
        for name in cls.split():
            assert re.search(rf"\.{re.escape(name)}\b", STYLESHEET), f"{sid}: .{name} has no style"
    assert "·" not in template  # meta text is separated by commas, not middle dots
    # Nothing in the file may LOAD from the network (a link to click, or an SVG namespace, is fine).
    lowered = template.lower()
    for fetch in ("src=", "url(", "@import", "<link", "<iframe", "<object"):
        assert fetch not in lowered, f"{sid}: {fetch} would load a remote resource"


def test_an_installed_unedited_report_skill_gets_its_template(isolated):
    store = SkillStore()
    starter = _starter("monthly-report")
    store.create({**starter, "author": "builtin"})

    assert starter_skill_files.write_starter_files("monthly-report") is True
    written = (isolated / "monthly-report" / "assets" / "report.html").read_text("utf-8")
    assert written == starter["files"]["assets/report.html"]
    assert store.find_by_id("monthly-report")["enabled"] is True
    assert starter_skill_files.write_starter_files("monthly-report") is False  # idempotent


def test_an_owner_edited_report_skill_is_left_alone(isolated):
    store = SkillStore()
    starter = _starter("weekly-report")
    store.create(
        {**starter, "body": starter["body"] + "\nAlso list my coffee spend.", "author": "builtin"}
    )

    assert starter_skill_files.write_starter_files("weekly-report") is False
    assert not (isolated / "weekly-report" / "assets").exists()


def test_a_copy_installed_before_the_template_is_upgraded_on_boot(isolated):
    """The owner's live copies: installed with the 2026-10-01 body and no assets folder."""
    store = SkillStore()
    old_body = "Produce the weekly report — the original from-scratch version."
    store.create({**_starter("weekly-report"), "body": old_body, "author": "builtin"})
    import hashlib

    digest = hashlib.sha256(old_body.encode()).hexdigest()
    skill_store._SUPERSEDED_STARTER_BODIES["weekly-report"] |= {digest}
    try:
        assert store.refresh_superseded_starters() == ["weekly-report"]
    finally:
        skill_store._SUPERSEDED_STARTER_BODIES["weekly-report"] -= {digest}

    assert starter_skill_files.sync_installed_starter_files() == ["weekly-report"]
    assert (isolated / "weekly-report" / "assets" / "report.html").is_file()
    assert "`references/slots.md`" in store.find_by_id("weekly-report")["body"]


def test_a_file_the_starter_no_longer_ships_is_removed_from_an_unedited_copy(isolated, monkeypatch):
    """A dropped bundled file would otherwise sit beside the files that replaced it."""
    monkeypatch.setattr(
        starter_skill_files, "_RETIRED_FILES", {"monthly-report": ("references/old.md",)}
    )
    store = SkillStore()
    store.create({**_starter("monthly-report"), "author": "builtin"})
    starter_skill_files.write_starter_files("monthly-report")
    stale = isolated / "monthly-report" / "references" / "old.md"
    stale.write_text("Superseded instructions.", "utf-8")

    assert starter_skill_files.write_starter_files("monthly-report") is True
    assert not stale.exists()
    assert (isolated / "monthly-report" / "references" / "slots.md").is_file()
    assert starter_skill_files.write_starter_files("monthly-report") is False
