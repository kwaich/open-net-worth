"""Regression: the vision tier reports live `read` progress per page (B20) — one call per
rendered page, so the owner sees "page i of N" instead of a silent multi-minute wait.

`report`'s own same-stage-updates-in-place rule (see test_statement_parse_progress.py) means
the FINAL snapshot only ever holds the LAST page's "read" entry — the per-page progression is
observed by snapshotting INSIDE the fake `_row_complete`, between pages, not by inspecting the
finished stages list afterward."""

from __future__ import annotations

import asyncio
import json

from backend.services.statements import parse_progress, tiers


def test_vision_reports_read_page_i_of_n(monkeypatch):
    monkeypatch.setattr("backend.services.vision_capability.supports_vision", lambda *a, **k: True)

    replies = [
        json.dumps({"meta": {"currency": "USD"}, "transactions": []}),
        json.dumps([]),
    ]
    calls: list = []
    mid_flight_snapshots: list = []

    async def _fake_row_complete(prompt, *, source, images=None):
        calls.append(images)
        # `report("read", page=i+1, ...)` already ran (tiers.py calls it BEFORE this), so
        # this snapshot is exactly what a poll landing between pages would see.
        mid_flight_snapshots.append(parse_progress.snapshot("tok-vision-0001"))
        return replies[len(calls) - 1]

    monkeypatch.setattr(tiers, "_row_complete", _fake_row_complete)
    images = [
        {"media_type": "image/png", "data": "aaa"},
        {"media_type": "image/png", "data": "bbb"},
    ]

    async def _run():
        with parse_progress.track("tok-vision-0001"):
            await tiers.tier3_vision(images)
            return parse_progress.snapshot("tok-vision-0001")

    final = asyncio.run(_run())

    assert len(calls) == 2
    page1_read = mid_flight_snapshots[0]["stages"][-1]
    page2_read = mid_flight_snapshots[1]["stages"][-1]
    assert (page1_read["stage"], page1_read["page"], page1_read["pages"]) == ("read", 1, 2)
    assert (page2_read["stage"], page2_read["page"], page2_read["pages"]) == ("read", 2, 2)

    # ONE entry throughout — page 2's report updated the SAME entry page 1's report opened,
    # never appended a second (the bug this stage's own "render" twin regressed on).
    final_reads = [s for s in final["stages"] if s["stage"] == "read"]
    assert len(final_reads) == 1
    assert final_reads[0]["page"] == 2 and final_reads[0]["pages"] == 2
