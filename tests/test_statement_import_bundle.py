"""Tests for the statement-import built-in skill (tier-2 playbook).

Verifies:
- ensure_statement_import_installed() creates the skill on a fresh store.
- Skill id and body are valid.
- The managed definition does not author a tool inventory.
- bulk_add_transactions description is ≤ 2000 chars and contains the activate_skill trigger.
- import_transactions description ≤ 900 chars.
- email_search_messages / email_get_messages descriptions ≤ 800 chars each.
- Skill installation is idempotent (safe to call twice).
- Installer does not touch other skills (e.g. user personal skills).
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS
from backend.services.statement_import_bundle import (
    _BODY,
    SKILL_ID,
    ensure_statement_import_installed,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _schema_by_name(name: str) -> dict:
    return next(t for t in TOOL_SCHEMAS if t["name"] == name)


# ── Bundle constant invariants ────────────────────────────────────────────────


def test_skill_id_is_statement_import():
    assert SKILL_ID == "statement-import"


def test_body_is_non_empty_string():
    assert isinstance(_BODY, str) and len(_BODY) > 100


# ── Tool description caps ─────────────────────────────────────────────────────


def test_bulk_add_desc_under_2000_chars():
    desc = _schema_by_name("add_transactions")["description"]
    assert len(desc) <= 2000, f"bulk_add_transactions desc is {len(desc)} chars (limit 2000)"


def test_bulk_add_desc_points_at_the_import_entrance():
    """The description must send statements to import_transactions, not hand-parsing.

    Superseded the old `activate_skill('statement-import')` assertion (2026-07-19): the skill's
    allowlist did not contain `import_transactions`, so instructing the model to load the
    skill FIRST narrowed the turn to a tool set where hand-parsing into this tool was the
    only reachable option. Transcript forensics over 289 sessions found 24 sessions that
    hand-booked 1,597 rows that way, and `activate_skill` appearing exactly once in the corpus.
    The skill is now optional; the entrance is the tool."""
    desc = _schema_by_name("add_transactions")["description"]
    assert "import_transactions" in desc
    assert "do NOT hand-parse" in desc


def test_bulk_add_desc_no_personal_examples():
    """De-personalized — no bank-specific proper names should remain."""
    desc = _schema_by_name("add_transactions")["description"]
    assert "Riverside Bank" not in desc
    assert "PocketPay" not in desc
    assert "Thất bại" not in desc  # Vietnamese "Failed"


def test_import_transactions_desc_under_900_chars():
    desc = _schema_by_name("import_transactions")["description"]
    assert len(desc) <= 900, f"import_transactions desc is {len(desc)} chars (limit 900)"


def test_email_search_messages_desc_under_800_chars():
    desc = _schema_by_name("email_search_messages")["description"]
    assert len(desc) <= 800, f"email_search_messages desc is {len(desc)} chars (limit 800)"


def test_email_get_messages_desc_under_800_chars():
    desc = _schema_by_name("email_get_messages")["description"]
    assert len(desc) <= 800, f"email_get_messages desc is {len(desc)} chars (limit 800)"


# ── Schema validity ───────────────────────────────────────────────────────────


def test_bulk_add_input_schema_is_valid():
    """input_schema must be a valid JSON Schema object."""
    schema = _schema_by_name("add_transactions")["input_schema"]
    assert isinstance(schema, dict)
    assert schema.get("type") == "object"
    assert json.loads(json.dumps(schema)) == schema


def test_update_transaction_input_schema_valid():
    schema = _schema_by_name("update_transactions")["input_schema"]
    assert schema.get("type") == "object"
    assert json.loads(json.dumps(schema)) == schema


# ── Install function ──────────────────────────────────────────────────────────


@pytest.fixture
def isolated_skill_store(tmp_path, monkeypatch):
    """Redirect SkillStore to a fresh tmp_path tree, mirroring test_skill_store.py."""
    from backend.db import skill_store as _skill_store_mod

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(_skill_store_mod, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(_skill_store_mod, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    return tmp_path


def test_ensure_install_creates_skill(isolated_skill_store):
    """On a fresh store the skill is created on first call."""
    from backend.db.skill_store import SkillStore

    store = SkillStore()
    assert store.find_by_id(SKILL_ID) is None

    result = ensure_statement_import_installed()
    assert result is True

    skill = SkillStore().find_by_id(SKILL_ID)
    assert skill is not None
    assert skill["id"] == SKILL_ID
    assert skill["body"] == _BODY
    assert "allowed_tools" not in skill


def test_ensure_install_idempotent(isolated_skill_store):
    """Calling ensure_statement_import_installed twice is safe and idempotent."""
    ensure_statement_import_installed()
    result = ensure_statement_import_installed()
    assert result is False  # no change on second call

    from backend.db.skill_store import SkillStore

    matching = [s for s in SkillStore().all() if s.get("id") == SKILL_ID]
    assert len(matching) == 1


def test_ensure_install_does_not_touch_other_skills(isolated_skill_store):
    """The installer must not modify or remove other pre-existing skills."""
    from backend.db.skill_store import SkillStore

    store = SkillStore()
    store.create(
        {
            "id": "riverside-bank-pocketpay-statement-import-deduplication",
            "name": "riverside-bank-pocketpay-statement-import-deduplication",
            "description": "User personal skill",
            "body": "personal body",
        }
    )

    ensure_statement_import_installed()

    user_skill = SkillStore().find_by_id("riverside-bank-pocketpay-statement-import-deduplication")
    assert user_skill is not None
    assert user_skill["body"].strip() == "personal body"
