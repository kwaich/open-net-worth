"""LLM fallback for CSV/XLSX column mapping (import_csv.suggest_mapping).

Contract: run the deterministic synonym heuristic (auto_detect_mapping) first; only call the LLM
(via core.llm_seam) when the heuristic can't cover the REQUIRED fields (date + description +
amount|inflow/outflow). The LLM answer is validated against the real columns (hallucinated columns
dropped), the heuristic wins where it already resolved a field, and any LLM failure fails OPEN to
the partial heuristic. Advisory only — the user still confirms the mapping in the form.
"""

from __future__ import annotations

import asyncio

import backend.core.llm_seam as llm_seam
from backend.services import import_csv


def _run(cols, **kw):
    return asyncio.run(import_csv.suggest_mapping(cols, **kw))


def test_heuristic_complete_skips_llm(monkeypatch):
    calls = {"n": 0}

    async def _fake_complete(*a, **k):
        calls["n"] += 1
        return "{}"

    monkeypatch.setattr(llm_seam, "complete", _fake_complete)
    out = _run(["Date", "Description", "Amount"])
    assert out["source"] == "heuristic"
    assert out["mapping"]["date"] == "Date"
    assert out["mapping"]["description"] == "Description"
    assert out["mapping"]["amount"] == "Amount"
    assert calls["n"] == 0  # heuristic covered the required fields → no LLM call


def test_llm_fallback_when_heuristic_incomplete(monkeypatch):
    async def _fake_complete(*a, **k):
        return '{"date": "c1", "description": "c2", "amount": "c3"}'

    monkeypatch.setattr(llm_seam, "complete", _fake_complete)
    out = _run(["c1", "c2", "c3"])  # opaque headers → heuristic maps nothing → LLM fires
    assert out["source"] == "ai"
    assert out["mapping"] == {"date": "c1", "description": "c2", "amount": "c3"}


def test_llm_hallucinated_column_is_dropped(monkeypatch):
    async def _fake_complete(*a, **k):
        return '{"date": "c1", "description": "c2", "amount": "NOPE"}'

    monkeypatch.setattr(llm_seam, "complete", _fake_complete)
    out = _run(["c1", "c2", "c3"])
    assert "amount" not in out["mapping"]  # column not in the file → dropped
    assert out["mapping"].get("date") == "c1"


def test_heuristic_wins_over_llm_on_a_resolved_field(monkeypatch):
    # "Date" is heuristically resolvable; the LLM disagrees — the heuristic must win.
    async def _fake_complete(*a, **k):
        return '{"date": "Weird", "description": "Weird", "amount": "Weird"}'

    monkeypatch.setattr(llm_seam, "complete", _fake_complete)
    out = _run(["Date", "Weird", "Other"])
    assert out["mapping"]["date"] == "Date"  # heuristic value preserved, not the LLM's


def test_llm_failure_fails_open_to_heuristic(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(llm_seam, "complete", _boom)
    out = _run(["c1", "c2", "c3"])
    assert out["source"] == "heuristic"  # failed open, no exception bubbled
    assert isinstance(out["mapping"], dict)


def test_allow_llm_false_never_calls(monkeypatch):
    calls = {"n": 0}

    async def _fake_complete(*a, **k):
        calls["n"] += 1
        return "{}"

    monkeypatch.setattr(llm_seam, "complete", _fake_complete)
    out = _run(["c1", "c2", "c3"], allow_llm=False)
    assert calls["n"] == 0
    assert out["source"] == "heuristic"


# ── endpoint: the columns_only probe returns the suggestion ──────────────────────


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.import_api import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _probe(client, csv_bytes):
    return client.post(
        "/api/statements/preview",
        files={"file": ("statement.csv", csv_bytes, "text/csv")},
        data={"format": "csv", "columns_only": "true"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )


def test_columns_only_returns_ai_suggestion_for_opaque_headers(monkeypatch):
    async def _fake_complete(*a, **k):
        return '{"date": "x1", "description": "x2", "amount": "x3"}'

    monkeypatch.setattr(llm_seam, "complete", _fake_complete)
    r = _probe(_client(), b"x1,x2,x3\n2026-01-01,Coffee,-5\n")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["columns"] == ["x1", "x2", "x3"]
    assert body["mapping_source"] == "ai"
    assert body["suggested_mapping"] == {"date": "x1", "description": "x2", "amount": "x3"}


def test_columns_only_normal_csv_uses_heuristic_no_llm(monkeypatch):
    calls = {"n": 0}

    async def _fake_complete(*a, **k):
        calls["n"] += 1
        return "{}"

    monkeypatch.setattr(llm_seam, "complete", _fake_complete)
    r = _probe(_client(), b"Date,Description,Amount\n2026-01-01,Coffee,-5\n")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mapping_source"] == "heuristic"
    assert body["suggested_mapping"]["date"] == "Date"
    assert calls["n"] == 0
