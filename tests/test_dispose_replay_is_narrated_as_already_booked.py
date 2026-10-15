"""An idempotent ``dispose_lots`` replay says so in words, not only in a flag.

ADR 2026-09-02 follow-up. The 2026-08-31 automation run narrated an idempotent ``dispose_lots``
hit as fresh work in its summary: the service answered ``{"ok": true, "idempotent": true, ...}``
and the tool passed that through bare, so a model reading ``ok`` counted a sale it had NOT just
booked. ``acquire_lots`` already carries an "ALREADY booked" ``_next_step`` on its replay path;
the dispose side did not, and a batched replay was folded into ``succeeded`` with nothing to
tell it apart from a new fill.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.db.position_store import PositionStore
from backend.db.realized_event_store import RealizedEventStore
from backend.services import lots as svc


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id="toolu_test"))
    return json.loads(text), proposed


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    return tmp_path


def _seed_position():
    return PositionStore().create(
        {"instrument_id": "inst-1", "account_id": "acc-1", "quantity": 0, "cost_currency": "USD"}
    )


def _seed_lot(position_id, qty=10):
    svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": position_id,
            "quantity": qty,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
        }
    )


def _sale(position_id, key, **over):
    return {
        "position_id": position_id,
        "quantity": 4,
        "proceeds_per_unit": 130,
        "proceeds_currency": "USD",
        "method": "fifo",
        "disposed_at": "2026-06-01",
        "idempotency_key": key,
        **over,
    }


def test_single_replay_carries_an_already_booked_note(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"])

    first, _ = call("dispose_lots", **_sale(pos["id"], "k-1"))
    assert "ALREADY" not in first.get("_next_step", "")

    second, _ = call("dispose_lots", **_sale(pos["id"], "k-1"))

    assert second["idempotent"] is True
    assert "ALREADY booked" in second["_next_step"]
    assert len(RealizedEventStore().all()) == 1


def test_batch_replay_is_counted_apart_from_new_work(isolated):
    pos = _seed_position()
    _seed_lot(pos["id"], qty=20)
    call("dispose_lots", **_sale(pos["id"], "k-1"))  # booked by an earlier run

    res, _ = call(
        "dispose_lots",
        disposals=[
            _sale(pos["id"], "k-1"),  # the retry
            _sale(pos["id"], "k-2", quantity=2, proceeds_per_unit=140, disposed_at="2026-07-01"),
        ],
    )

    assert res["total"] == 2
    assert res["failed"] == 0
    assert res["already_booked"] == 1
    assert "ALREADY booked" in res["results"][0]["_next_step"]
    assert "already booked" in res["note"].lower()
    assert len(RealizedEventStore().all()) == 2
