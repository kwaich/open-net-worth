"""Structured-output framework: complete()/complete_json()/complete_model() + the
shared balanced-brace parser and the provider degrade ladder.

These pin the PROVIDER-INDEPENDENT guarantee (validate + reask + tolerant parse), which
is what keeps the background flows robust regardless of which provider/params are in play.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

import pytest
from pydantic import BaseModel, Field

from backend.ai import structured
from backend.ai.memory import _extract_balanced_json, _parse_json
from backend.core import llm_seam


@pytest.fixture
def seam(monkeypatch):
    """Register a scripted completion hook on the seam; return the call log + a setter."""
    log: list[dict] = []
    replies: list[str] = []

    async def fake(prompt, *, system="", model="", max_tokens=2048, source="x", **kw):
        log.append(
            {
                "prompt": prompt,
                "system": system,
                "rf": kw.get("response_format"),
                "effort": kw.get("effort"),
            }
        )
        return replies.pop(0) if replies else "{}"

    monkeypatch.setattr(llm_seam, "_complete", fake)
    return {"log": log, "replies": replies}


# ── balanced-brace parser ──────────────────────────────────────────────────────


def test_parser_survives_reasoning_preamble_and_trailing_prose():
    text = 'Let me think...\n{"materiality": "new", "why": "x"}\nHope that helps!'
    assert _parse_json(text) == {"materiality": "new", "why": "x"}


def test_parser_returns_none_on_truncated_json():
    assert _parse_json('reasoning...\n{"a": 1, "b": [1, 2') is None


def test_parser_extracts_array_and_respects_strings():
    assert _parse_json('sure:\n[{"t": "a } b"}]') == [{"t": "a } b"}]
    assert _extract_balanced_json("no json here") is None


# ── complete_json: json mode, effort, validate + reask, skip-on-failure ─────────


def test_complete_json_sends_json_object_reasoning_enabled_by_default(seam):
    seam["replies"].append('{"ok": true}')
    out = asyncio.run(llm_seam.complete_json("do it", system="return json"))
    assert out == {"ok": True}
    assert seam["log"][0]["rf"] == {"type": "json_object"}
    # Reasoning STAYS ENABLED by default (effort="") — no reasoning-disable param is sent;
    # the tolerant parse strips any thinking out of the reply instead (owner decision).
    assert seam["log"][0]["effort"] in (None, "")


def test_complete_json_reasks_then_succeeds(seam):
    seam["replies"] += ["garbage, not json", '{"materiality": "new"}']

    def validate(o):
        return None if isinstance(o, dict) and o.get("materiality") else "missing materiality"

    out = asyncio.run(llm_seam.complete_json("go", system="json", validate=validate, max_reasks=1))
    assert out == {"materiality": "new"}
    assert len(seam["log"]) == 2  # reask happened
    assert "invalid" in seam["log"][1]["prompt"]  # the reask fed the error back


def test_complete_json_returns_none_and_logs_after_budget(seam, caplog):
    seam["replies"] += ["nope", "still nope"]
    with caplog.at_level("WARNING"):
        out = asyncio.run(llm_seam.complete_json("go", system="json", max_reasks=1))
    assert out is None
    assert any("no valid JSON" in r.message for r in caplog.records)


def test_complete_json_with_schema_sends_json_schema(seam):
    seam["replies"].append('{"x": 1}')
    asyncio.run(llm_seam.complete_json("go", system="json", schema={"type": "object"}))
    rf = seam["log"][0]["rf"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True


# ── complete_model: Pydantic-derived schema, validate, typed result ─────────────


class _Analysis(BaseModel):
    why: str = Field(min_length=1)
    materiality: str = ""
    actions: List[str] = Field(default_factory=list)


def test_complete_model_returns_validated_instance(seam):
    seam["replies"].append('{"why": "Uber bid confirmed", "materiality": "new"}')
    out: Optional[_Analysis] = asyncio.run(structured.complete_model("analyze", _Analysis))
    assert isinstance(out, _Analysis)
    assert out.why == "Uber bid confirmed" and out.materiality == "new"
    # A schema was derived from the model and sent natively.
    assert seam["log"][0]["rf"]["type"] == "json_schema"


def test_complete_model_reasks_on_validation_error(seam):
    seam["replies"] += ['{"why": ""}', '{"why": "real analysis"}']  # empty why fails min_length
    out = asyncio.run(structured.complete_model("analyze", _Analysis, max_reasks=1))
    assert out is not None and out.why == "real analysis"
    assert len(seam["log"]) == 2


def test_complete_model_returns_none_when_unfixable(seam):
    seam["replies"] += ['{"why": ""}', '{"why": ""}']  # never satisfies min_length
    out = asyncio.run(structured.complete_model("analyze", _Analysis, max_reasks=1))
    assert out is None
