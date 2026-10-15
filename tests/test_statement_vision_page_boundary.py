"""Regression test: the vision tier must never drop a row at a page boundary just because it
looks identical to the previous page's last row. Each page is a separately rendered image —
pages cannot overlap, so an identical row at the boundary is a real, repeated charge (e.g. three
vending-machine swipes at the bottom of page 1, a fourth at the top of page 2), never a re-read
artifact. See docs/adr/2026-09-08-an-identical-row-at-a-page-boundary-is-a-real-charge.md."""

from __future__ import annotations

import asyncio
import json

from backend.core import llm_seam
from backend.services.statements.parser import parse_statement

_BOUNDARY_ROW = {
    "date": "2026-03-05",
    "amount": 1.50,
    "direction": "OUT",
    "payee": "Vending Co",
    "description": "VENDING MACHINE 12 TESTVILLE",
    "confidence": 0.9,
}

_PAGE1_REPLY = {
    "meta": {"issuer": "Testville Bank", "currency": "USD"},
    "transactions": [
        {**_BOUNDARY_ROW, "date": "2026-03-04"},
        {**_BOUNDARY_ROW},  # last row of page 1
    ],
}

_PAGE2_REPLY = [
    {**_BOUNDARY_ROW},  # identical to page 1's last row — a real, separate charge
    {
        "date": "2026-03-06",
        "amount": 42.00,
        "direction": "OUT",
        "payee": "Example Grocer",
        "description": "EXAMPLE GROCER TESTVILLE",
        "confidence": 0.9,
    },
]


def test_vision_never_drops_an_identical_row_at_a_page_boundary(monkeypatch):
    monkeypatch.setattr("backend.services.vision_capability.supports_vision", lambda *a, **k: True)
    replies = [json.dumps(_PAGE1_REPLY), json.dumps(_PAGE2_REPLY)]
    calls: list = []

    async def _complete(prompt, **kwargs):
        calls.append(kwargs.get("images"))
        return replies[len(calls) - 1]

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    pages = [{"media_type": "image/png", "data": "aaa"}, {"media_type": "image/png", "data": "bbb"}]
    result = asyncio.run(parse_statement({"kind": "pdf_pages", "images": pages}))

    assert len(result.rows) == 4
    boundary_matches = [
        r
        for r in result.rows
        if r.date == "2026-03-05" and r.description == _BOUNDARY_ROW["description"]
    ]
    assert len(boundary_matches) == 2
