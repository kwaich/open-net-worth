"""Regression (B9): a slow statement-pipeline call must NOT freeze the event loop.

``preview_import`` (backend/api/import_api.py) is ``async def`` but used to call the sync
decision pipeline directly on the loop thread — so a multi-page scanned-PDF preview (one
vision call per page, ~90s each) froze the whole server: even ``GET /api/health`` couldn't
complete. The fix offloads the sync pipeline call via ``asyncio.to_thread``.

The bug is a genuinely-blocking (non-yielding) call on the ONE event-loop thread, so it can
only be observed the way production hit it: a second real request arriving while the first
is mid-flight. ``TestClient`` used as a context manager shares ONE background event loop
(an anyio portal) across every call made through it — issuing the slow preview from a
background Python thread and the health check from the test's own thread reproduces that
exact contention. Synthetic CSV bytes only, no real data."""

from __future__ import annotations

import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.services.statements.decisions as decisions_module
from backend.api.import_api import router as import_router

_HDR = {"X-Requested-With": "XMLHttpRequest"}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(import_router)

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


def test_preview_import_does_not_freeze_the_event_loop(monkeypatch):
    release = threading.Event()
    entered = threading.Event()

    def slow_preview(*args, **kwargs):
        entered.set()
        release.wait(timeout=3)  # safety net only — the test itself sets `release` well before this
        return {"statement_id": "stmt_test", "rows": []}

    monkeypatch.setattr(decisions_module, "decision_preview", slow_preview)

    app = _make_app()
    csv_bytes = b"date,amount,description\n2026-01-01,1.00,test\n"

    with TestClient(app) as client:
        result: dict = {}

        def _run_preview() -> None:
            files = {"file": ("statement.csv", csv_bytes, "text/csv")}
            result["resp"] = client.post("/api/statements/preview", files=files, headers=_HDR)

        worker = threading.Thread(target=_run_preview)
        worker.start()
        assert entered.wait(timeout=5), "preview never reached the blocking pipeline call"

        start = time.monotonic()
        health_resp = client.get("/api/health")
        elapsed = time.monotonic() - start

        assert health_resp.status_code == 200
        assert elapsed < 3.0, f"health request took {elapsed:.2f}s — event loop was blocked"
        assert not release.is_set(), "preview must still be blocked when health returned"

        release.set()
        worker.join(timeout=5)
        assert result["resp"].status_code == 200
        assert result["resp"].json()["statement_id"] == "stmt_test"
