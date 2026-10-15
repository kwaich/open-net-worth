"""Endpoint smoke for statement-preview live progress (B20): `progress_token` on
`POST /api/statements/preview` (and its csv/preview alias) + `GET
/api/statements/preview-progress/{token}` polled while the POST is still in flight.

TestClient used as a CONTEXT MANAGER, matching test_import_api_event_loop.py's precedent:
that shares ONE background event loop (an anyio portal) across every call made through it,
which is what lets a background-thread POST and the test's own GET run concurrently without
each spinning up its own loop. `decisions.decision_preview` is stubbed to a slow function that
calls `parse_progress.report` itself — this is a progress-plumbing test, not a parser test."""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.services.statements.decisions as decisions_module
from backend.api.import_api import router as import_router
from backend.api.statement_parse_api import router as statement_parse_router
from backend.services.statements import parse_progress

_HDR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture(autouse=True)
def _clean_channels():
    parse_progress._reset_for_tests()
    yield
    parse_progress._reset_for_tests()


_CSV = b"date,amount,description\n2026-01-01,1.00,test\n"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(import_router)
    app.include_router(statement_parse_router)
    return app


def _files():
    return {"file": ("bank.csv", _CSV, "text/csv")}


def _slow_preview(started: threading.Event, release: threading.Event):
    def _fake(*args, **kwargs):
        parse_progress.report("match")
        started.set()
        release.wait(5)
        parse_progress.report("lucius")
        return {"statement_id": "stmt_test0001", "rows": []}

    return _fake


def test_progress_token_shows_a_live_stage_then_completes(monkeypatch):
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(decisions_module, "decision_preview", _slow_preview(started, release))

    with TestClient(_make_app()) as client:
        result: dict = {}

        def _post():
            result["resp"] = client.post(
                "/api/statements/csv/preview",
                files=_files(),
                data={"mapping": "{}", "progress_token": "prog-live-00001"},
                headers=_HDR,
            )

        worker = threading.Thread(target=_post)
        worker.start()
        assert started.wait(5)

        live = client.get("/api/statements/preview-progress/prog-live-00001")
        assert live.status_code == 200
        body = live.json()
        assert body["stages"][-1]["stage"] == "match"
        assert body["done"] is False

        release.set()
        worker.join(5)
        assert result["resp"].status_code == 200

        after = client.get("/api/statements/preview-progress/prog-live-00001").json()
    assert after["done"] is True
    assert [s["stage"] for s in after["stages"]] == ["match", "lucius"]


def test_progress_endpoint_never_carries_row_data(monkeypatch):
    started, release = threading.Event(), threading.Event()
    release.set()  # never actually blocks — this test only checks the snapshot's shape
    monkeypatch.setattr(decisions_module, "decision_preview", _slow_preview(started, release))

    with TestClient(_make_app()) as client:
        client.post(
            "/api/statements/csv/preview",
            files=_files(),
            data={"mapping": "{}", "progress_token": "prog-shape-0001"},
            headers=_HDR,
        )
        body = client.get("/api/statements/preview-progress/prog-shape-0001").json()
    assert set(body) == {"stages", "started_at", "elapsed_s", "done", "error", "statement_id"}
    for stage in body["stages"]:
        assert set(stage) == {"stage", "label", "started_at", "done_at", "page", "pages", "retry"}


def test_bad_progress_token_is_422():
    with TestClient(_make_app()) as client:
        resp = client.post(
            "/api/statements/csv/preview",
            files=_files(),
            data={"mapping": "{}", "progress_token": "!!!short!!!"},
            headers=_HDR,
        )
    assert resp.status_code == 422


def test_unknown_progress_token_is_404():
    with TestClient(_make_app()) as client:
        resp = client.get("/api/statements/preview-progress/never-existed-0000001")
    assert resp.status_code == 404


def test_preview_without_a_token_is_unaffected(monkeypatch):
    started, release = threading.Event(), threading.Event()
    release.set()
    monkeypatch.setattr(decisions_module, "decision_preview", _slow_preview(started, release))
    with TestClient(_make_app()) as client:
        resp = client.post(
            "/api/statements/csv/preview",
            files=_files(),
            data={"mapping": "{}"},
            headers=_HDR,
        )
    assert resp.status_code == 200
    assert parse_progress._channels == {}
