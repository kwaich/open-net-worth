"""Manual scan response distinguishes pending analysis and failed synthesis."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from backend import main
from backend.monitoring import intel_agent


def test_manual_scan_reports_pending_batch(monkeypatch):
    async def pending(**kwargs):
        raise intel_agent.IntelScanPending("processing")

    monkeypatch.setattr(intel_agent, "run_intel_scan", pending)
    response = asyncio.run(main.trigger_intel_scan())
    assert response.status_code == 202
    assert json.loads(response.body) == {"ok": False, "pending": True}


def test_manual_scan_reports_incomplete_analysis(monkeypatch):
    async def incomplete(**kwargs):
        raise intel_agent.IntelScanIncomplete("analysis failed")

    monkeypatch.setattr(intel_agent, "run_intel_scan", incomplete)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.trigger_intel_scan())
    assert exc.value.status_code == 503
