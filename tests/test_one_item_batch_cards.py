# ruff: noqa: F811  (the shared `isolated` fixture is imported, then requested by name)
"""A batch of ONE draws the tool's real card, not a one-row batch table (synthetic data only).

Found live 2026-10-09: the schemas advertise only the batch array, so a model sends
``updates=[…]`` / ``disposals=[…]`` even for one item — and every single edit drew a "batch"
card of raw ids, hiding a move's from→to accounts and a sale's realized gain and destination.
``registry.one_item`` routes a one-item array to the tool's single path; approval re-runs on
the same staged args and re-verifies the card it showed.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import _confirm_gate as _gate
from backend.ai.tools import execute_tool, registry
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.services import positions as positions_service
from tests.test_position_figures import _account, _instrument, isolated  # noqa: F401

_TOOL_USE_ID = "toolu_one_item_card"


@pytest.fixture
def attended(monkeypatch):
    monkeypatch.setattr(
        registry, "should_preview_write", lambda: not registry.is_redeeming_approval()
    )


def call(tool: str, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


def approve(tool: str):
    return call(tool, **{_gate.CONFIRM_TOKEN_KEY: _TOOL_USE_ID})


def _holding(account: dict) -> str:
    return positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": _instrument()["id"],
            "account_id": account["id"],
            "quantity": 10,
            "cost_basis": 5.0,
            "acquired_at": "2025-01-02",
        }
    )["position_id"]


def test_a_one_item_move_shows_from_and_to_accounts(isolated, attended):
    src, dst = _account("Old Broker"), _account("New Broker")
    pid = _holding(src)

    res, proposed = call(
        "update_positions", updates=[{"position_id": pid, "changes": {"account_id": dst["id"]}}]
    )

    assert res is None and proposed["type"] == "update"
    assert (proposed["display"]["account_from"], proposed["display"]["account_to"]) == (
        "Old Broker",
        "New Broker",
    )
    res, _ = approve("update_positions")
    assert res["ok"] is True
    assert {lot["account_id"] for lot in LotStore().lots_for_position(pid)} == {dst["id"]}


def test_a_one_item_sale_shows_its_realized_gain(isolated, attended):
    pid = _holding(_account())

    res, proposed = call(
        "dispose_lots",
        disposals=[
            {
                "position_id": pid,
                "quantity": 4,
                "proceeds_per_unit": 8.0,
                "proceeds_currency": "SGD",
                "disposed_at": "2025-06-01",
            }
        ],
    )

    assert res is None and proposed["type"] == "lot_dispose"
    assert proposed["display"]["realized_gain"] == pytest.approx(12.0)  # 4 × (8 − 5)
    res, _ = approve("dispose_lots")
    assert res.get("ok") is True, res
    assert PositionStore().find_by_id(pid)["quantity"] == 6.0


def test_two_items_still_draw_one_batch_card(isolated, attended):
    a, b = _holding(_account("A")), _holding(_account("B"))

    res, proposed = call(
        "update_positions",
        updates=[
            {"position_id": a, "changes": {"note": "x"}},
            {"position_id": b, "changes": {"note": "y"}},
        ],
    )

    assert res is None and proposed["type"] == "update_batch"
    assert proposed["display"]["count"] == 2
