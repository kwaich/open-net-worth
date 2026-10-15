"""
AI tool-dispatch tests for the learn-from-corrections suggestions (RFC P2-SP2).

Offline + isolated: the CorrectionStore + RuleStore + transaction store paths are
monkeypatched into tmp_path; execute_tool constructs the stores per call so the patch
takes effect. The profile path is isolated so category validation sees only built-ins.
execute_tool is async, driven via asyncio.run. No LLM, no network.

These give Lucius parity with the "Suggested rules" UI block: get_rule_suggestions
(read) + dismiss_rule_suggestions (suppress). Accepting a suggestion reuses the existing
create_rules tool (no new write path) — covered in tests/test_rule_tools.py.

Run:
    .venv/bin/pytest tests/test_correction_tools.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.db.correction_store import CorrectionStore


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    return tmp_path


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def _seed_corrections(n=3, payee="STARBUCKS", to="food"):
    s = CorrectionStore()
    for _ in range(n):
        s.record(
            {
                "payee_key": payee.lower(),
                "payee_sample": payee,
                "txn_id": "t",
                "from_category": "shopping",
                "to_category": to,
                "kind": "expense",
            }
        )


def test_correction_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {"get_rule_suggestions", "dismiss_rule_suggestions"} <= names


def test_get_rule_suggestions_empty(isolated):
    res, proposed = call("get_rule_suggestions")
    assert res["ok"] is True and res["suggestions"] == []
    assert proposed is None


def test_get_rule_suggestions_returns_suggestion(isolated):
    _seed_corrections(3)
    res, proposed = call("get_rule_suggestions")
    assert res["ok"] is True
    assert proposed is None
    assert len(res["suggestions"]) == 1
    s = res["suggestions"][0]
    assert s["payee_key"] == "starbucks"
    assert s["target_category"] == "food"
    assert s["count"] == 3
    assert s["suggested_rule"]["set"]["category"] == "food"


def test_dismiss_rule_suggestion_suppresses(isolated):
    _seed_corrections(3)
    res, proposed = call("dismiss_rule_suggestions", payee_key="starbucks", target_category="food")
    assert res["ok"] is True and proposed is None
    # after dismissal, the suggestion is gone
    res2, _ = call("get_rule_suggestions")
    assert res2["suggestions"] == []


def test_dismiss_rule_suggestion_requires_fields(isolated):
    res, _ = call("dismiss_rule_suggestions", payee_key="")
    assert res["ok"] is False
