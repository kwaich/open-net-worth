"""Statement-format skills are ORDINARY skills (owner decision 2026-07-12).

No bespoke mechanism: a skill created through the normal SkillStore (⇒ create_skill tool, the
Skills UI, the / picker) with ``metadata.statement_format`` steers the statement parser — its
BODY rides into the extraction prompt as guidance, scoped per account, disabled skills
ignored, deleted skills forgotten. The parser bridge (services/statements/skills_service) is
read-only glue; the four bespoke draft/save/list/delete tools are GONE."""

from __future__ import annotations

import json

import pytest

# Collection-time import (py3.9): backend.ai.memory builds asyncio.Lock() at module import.
from backend.ai.tools import TOOL_SCHEMAS
from backend.core import llm_seam
from backend.services import ledger as ledger_service
from backend.services.statements import decisions, skills_service


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.db.skill_store as skill_store_mod
    import backend.db.statement_store as stmt_mod

    # PayeeStore is DB-backed now (plan 4d task 2) — db_isolated redirects the process-wide
    # singleton, so payee_registry._store resolves against this fixture's isolated database
    # with no per-instance path-sync needed.
    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    (tmp_path / "statements").mkdir()
    # Fix 3 (security review round 2, consolidate-derived-data-storage): every consumer of
    # the statements blob directory now reads through
    # backend.core.paths.statements_files_dir() — patch its ONE real binding.
    import backend.core.paths as core_paths_mod

    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", tmp_path / "statements")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store_mod, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store_mod, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(llm_seam, "_parse_json", None)
    ledger_service.accounts.insert(
        {"id": "acc", "name": "Card", "kind": "liability", "currency": "SGD"}
    )


def _make_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


_EXAMPLE_PDF = _make_pdf("STATEMENT\nCOFFEE PLACE 4.50\nSALARY CO 2500.00")
_ROWS = [
    {
        "date": "2026-01-02",
        "amount": 4.50,
        "direction": "OUT",
        "payee": "Coffee Place",
        "description": "COFFEE PLACE 4.50",
        "confidence": 0.95,
    },
]


def _stub(monkeypatch, capture: list | None = None):
    async def _complete(prompt, **kwargs):
        if capture is not None:
            capture.append(prompt)
        if kwargs.get("source") == "statement_metadata":
            return json.dumps({})
        return json.dumps(_ROWS)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def _create_skill(**over):
    from backend.db.skill_store import SkillStore

    data = {
        "name": "statement-format: test bank",
        "description": "How to read Test Bank statements",
        "body": "Charges appear WITHOUT a sign; a trailing CR marks refunds. Skip loyalty rows.",
        "metadata": {"statement_format": "true", "account_id": "acc"},
    }
    data.update(over)
    return SkillStore().create(data)


def test_ordinary_skill_guidance_rides_into_the_extraction_prompt(monkeypatch):
    _create_skill()
    prompts: list = []
    _stub(monkeypatch, capture=prompts)
    res = decisions.decision_preview(_EXAMPLE_PDF, "pdf", "acc", filename="x.pdf")
    # the LAST call may now be the enrich-time category-hint batch — assert against the
    # row-EXTRACTION prompt specifically (the guidance's destination)
    row_prompt = next(p for p in prompts if "Extract every completed transaction" in p)
    assert "The user taught the following" in row_prompt
    assert "trailing CR marks refunds" in row_prompt  # the skill BODY, verbatim
    assert res["meta"]["extra"]["statement_skills"] == ["statement-format-test-bank"]


def test_account_scoping_and_global_fallback(monkeypatch):
    _create_skill(
        name="statement-format: other bank",
        metadata={"statement_format": "true", "account_id": "someone-else"},
    )
    guidance, names = skills_service.guidance_for("acc")
    assert guidance is None and names == []  # scoped to another account → not applied
    _create_skill(
        name="statement-format: global",
        metadata={"statement_format": "true"},
        body="Global guidance body.",
    )
    guidance, names = skills_service.guidance_for("acc")
    assert "Global guidance body." in (guidance or "")
    assert names == ["statement-format-global"]  # skill names normalize to slugs


def test_disabled_and_unmarked_skills_are_ignored(monkeypatch):
    _create_skill(enabled=False)
    _create_skill(name="ordinary skill", metadata={}, body="not a statement skill")
    guidance, names = skills_service.guidance_for("acc")
    assert guidance is None and names == []


def test_deleting_the_skill_forgets_it_like_any_other_skill(monkeypatch):
    from backend.db.skill_store import SkillStore

    skill = _create_skill()
    prompts: list = []
    _stub(monkeypatch, capture=prompts)
    decisions.decision_preview(_EXAMPLE_PDF, "pdf", "acc", filename="x.pdf")
    assert any("The user taught the following" in p for p in prompts)
    SkillStore().delete(skill["id"])
    prompts.clear()
    decisions.decision_preview(_EXAMPLE_PDF, "pdf", "acc", filename="y.pdf")
    assert not any("The user taught the following" in p for p in prompts)


def test_no_bespoke_statement_skill_tools_exist():
    # The owner's correction: statement skills ride the ONE skills system — the briefly-added
    # bespoke tool surface must stay gone.
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert (
        not {
            "draft_statement_skill",
            "save_statement_skill",
            "list_statement_skills",
            "delete_statement_skill",
        }
        & names
    )
    assert {"create_skill", "list_skills", "delete_skill"} <= names  # the real surface
