"""Agent Skills open-standard compliance (agentskills.io/specification).

ONW must be strictly compliant with the standard SKILL.md format and must not emit a
second ONW-only skill language.
These tests pin:
  * validate_skill_md_frontmatter() = the in-repo `skills-ref validate` equivalent (every spec
    rule: name pattern/length, description length/non-empty, compatibility ≤500, metadata
    string-map);
  * round-trip preservation — license / compatibility / metadata survive
    build_skill_md → parse_skill_md. The ONE standard field deliberately dropped is the
    experimental ``allowed-tools``: a skill carries no tool list (ADR
    2026-09-26-skills-carry-no-allowed-tools) — an imported file with it is accepted, the
    field is discarded;
  * every shipped STARTER skill exports to a spec-COMPLIANT SKILL.md;
  * non-standard legacy fields are ignored rather than emitted.

Run: .venv/bin/pytest tests/test_skill_md_spec_compliance.py -v
"""

from __future__ import annotations

from backend.db.skill_store import STARTER_SKILLS, SkillStore
from backend.services.skill_bundles import (
    build_skill_md,
    parse_skill_md,
    validate_skill_md_frontmatter,
)

# ── validate_skill_md_frontmatter: the spec rules ──────────────────────────────


def test_minimal_valid_frontmatter():
    assert validate_skill_md_frontmatter({"name": "pdf-processing", "description": "x"}) == []


def test_full_valid_frontmatter():
    meta = {
        "name": "pdf-processing",
        "description": "Extract PDF text. Use when handling PDFs.",
        "license": "Apache-2.0",
        "compatibility": "Designed for Open Net Worth",
        "metadata": {"author": "example-org", "version": "1.0"},
        "allowed-tools": "Bash(git:*) Read",
    }
    assert validate_skill_md_frontmatter(meta) == []


def test_name_rules():
    bad = ["PDF-Processing", "-pdf", "pdf-", "pdf--processing", "", "a" * 65]
    for n in bad:
        assert validate_skill_md_frontmatter({"name": n, "description": "x"}), n
    good = ["pdf-processing", "data-analysis", "runway", "a", "a1-b2-c3"]
    for n in good:
        assert validate_skill_md_frontmatter({"name": n, "description": "x"}) == [], n


def test_description_required_and_bounded():
    assert validate_skill_md_frontmatter({"name": "x", "description": ""})
    assert validate_skill_md_frontmatter({"name": "x", "description": "   "})
    assert validate_skill_md_frontmatter({"name": "x", "description": "a" * 1025})
    assert validate_skill_md_frontmatter({"name": "x", "description": "a" * 1024}) == []


def test_compatibility_and_metadata_typing():
    assert validate_skill_md_frontmatter(
        {"name": "x", "description": "d", "compatibility": "a" * 501}
    )
    assert validate_skill_md_frontmatter(
        {"name": "x", "description": "d", "metadata": {"k": 1}}  # non-string value
    )
    assert validate_skill_md_frontmatter(
        {"name": "x", "description": "d", "metadata": ["not", "a", "map"]}
    )


def test_allowed_tools_in_any_shape_never_rejects_a_skill():
    """The field is discarded, so its shape can't make an imported skill invalid."""
    assert (
        validate_skill_md_frontmatter(
            {"name": "x", "description": "d", "allowed-tools": ["Read", "Bash(git:*)"]}
        )
        == []
    )


# ── round-trip: nothing the standard defines is dropped ────────────────────────


def test_build_skill_md_round_trips_all_standard_fields():
    record = {
        "name": "round-trip-all",
        "description": "A standards round-trip test.",
        "license": "Apache-2.0",
        "compatibility": "Requires Open Net Worth",
        "metadata": {"author": "you", "version": "2.0"},
        "body": "do it",
    }
    md = build_skill_md(record)
    # The emitted SKILL.md is itself spec-compliant.
    meta, body = parse_skill_md(md)
    assert validate_skill_md_frontmatter(meta) == []
    assert meta["license"] == "Apache-2.0"
    assert meta["compatibility"] == "Requires Open Net Worth"
    # User metadata (non-onw-*) round-trips through the metadata map.
    assert meta["metadata"]["author"] == "you"
    assert meta["metadata"]["version"] == "2.0"
    assert body.strip() == "do it"


def test_build_skill_md_drops_non_standard_controls():
    record = {
        "name": "scoped",
        "description": "A scoped skill.",
        "allowed_tools": ["get_transactions"],
        "blocked_tools": ["bulk_delete_transactions"],
        "parameters": [
            {
                "name": "month",
                "type": "string",
                "required": False,
                "description": "YYYY-MM",
                "default": "",
            }
        ],
        "trigger": "schedule",
        "guardrails": {"tier_overrides": {"add_transaction": "approve"}},
        "body": "body",
    }
    md = build_skill_md(record)
    meta, _ = parse_skill_md(md)
    assert validate_skill_md_frontmatter(meta) == []
    assert set(meta) == {"name", "description"}


def test_pure_skill_frontmatter_has_only_name_description():
    """A pure skill (no ONW controls) must emit exactly {name, description}."""
    md = build_skill_md({"name": "pure", "description": "d", "body": "b"})
    meta, _ = parse_skill_md(md)
    assert set(meta.keys()) == {"name", "description"}


def test_allowed_tools_is_never_written_to_frontmatter():
    """The rendered SKILL.md is what the model reads on activation; a tool list there read as
    the model's whole toolset (a live chat saw 4 names, found 1 loaded, and stopped looking)."""
    md = build_skill_md(
        {"name": "scoped", "description": "d", "allowed_tools": ["get_transactions"]}
    )
    meta, _ = parse_skill_md(md)
    assert "allowed-tools" not in meta


def test_parametrized_legacy_skill_does_not_emit_metadata():
    md = build_skill_md(
        {
            "name": "param-skill",
            "description": "d",
            "parameters": [
                {
                    "name": "month",
                    "type": "string",
                    "required": False,
                    "description": "",
                    "default": "",
                }
            ],
        }
    )
    meta, _ = parse_skill_md(md)
    assert set(meta) == {"name", "description"}


# ── every shipped starter exports compliant ────────────────────────────────────


def test_all_starters_export_compliant_skill_md():
    for s in STARTER_SKILLS:
        meta, _ = parse_skill_md(build_skill_md(s))
        problems = validate_skill_md_frontmatter(meta)
        assert problems == [], f"{s['id']}: {problems}"


def test_created_skill_persists_standard_fields(tmp_path, monkeypatch):
    from backend.db import skill_store

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    sk = SkillStore().create(
        {
            "name": "Imported Skill",  # normalized to a valid slug
            "description": "d",
            "compatibility": "Requires Open Net Worth",
            "metadata": {"author": "acme", "version": 3},  # value coerced to str
            "allowed_tools": ["get_transactions"],
        }
    )
    assert sk["name"] == "imported-skill"
    assert sk["compatibility"] == "Requires Open Net Worth"
    # User metadata (non-onw-*) round-trips; value coerced to str
    assert sk["metadata"] == {"author": "acme", "version": "3"}
    # allowed_tools is not a skill field: accepted from a caller, never stored
    assert "allowed_tools" not in sk
    assert "allowed-tools" not in (skills_dir / "imported-skill" / "SKILL.md").read_text()
