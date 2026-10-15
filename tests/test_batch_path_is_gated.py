"""A batch write is gated exactly like a single one — the shape of the arguments is never
the thing that decides whether the owner is asked.

Seven tools used to compute ``gated = should_preview_write()`` and then RETURN from their
batch branch before anything read it. So ``add_positions(instrument_id=…)`` raised a card and
``add_positions(adds=[<the same one item>])`` wrote straight through — the same tool, the
same effect, one argument name apart. Each tool's own description told the model where the
hatch was ("pass ``adds`` (a list) instead — always applies immediately"), which made the
MODEL the authority on whether a write is confirmed. ADR
``2026-08-13-mcp-permission-config-is-the-single-authority`` says it must never be.

Found in use, not by reading: a two-position add wrote straight through on an attended turn
(owner, 2026-08-27).

Closing it costs nothing. The case the hatch was built for — a bulk import with nobody
watching — is ALREADY ungated, because ``should_preview_write`` answers False on an
unattended surface; the hatch only ever skipped the card while a human WAS present. And a
batch is one call, so it raises ONE card for the whole batch, never N.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool, registry

_TOOL_USE_ID = "toolu_batch_gate_test"


@pytest.fixture(autouse=True)
def _card_capable(monkeypatch):
    """A surface that CAN show a card — still yielding to a redemption, so a ✓ writes."""
    monkeypatch.setattr(
        registry, "should_preview_write", lambda: not registry.is_redeeming_approval()
    )


def call(tool: str, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


# (tool, batch field, one item) — every tool that had an ungated batch branch.
BATCHES = [
    ("add_positions", "adds", {"instrument_id": "inst-1", "quantity": 1, "cost_basis": 10}),
    ("update_positions", "updates", {"position_id": "pos-1", "changes": {"note": "x"}}),
    ("remove_positions", "removals", "pos-1"),
    ("acquire_lots", "acquisitions", {"position_id": "pos-1", "quantity": 1, "unit_cost": 10}),
    ("dispose_lots", "disposals", {"position_id": "pos-1", "quantity": 1}),
    ("delete_lots", "deletions", {"lot_id": "lot-1"}),
    ("undo_lot_disposals", "event_ids", "evt-1"),
]


@pytest.mark.parametrize("tool,field,item", BATCHES, ids=[b[0] for b in BATCHES])
def test_a_batch_call_stages_a_card_instead_of_writing(tool, field, item, db_isolated):
    """THE regression guard: the batch branch must reach the gate, not return around it.
    Two items — a batch of ONE takes the tool's single path (registry.one_item), whose own
    gate is covered by test_a_batch_of_one_never_writes_ungated below."""
    text, proposed = call(tool, **{field: [item, item]})
    assert text is None, f"{tool} wrote through its batch path instead of staging a card"
    assert proposed is not None and proposed["tool_use_id"] == _TOOL_USE_ID
    assert proposed["grant_tool"] == tool


@pytest.mark.parametrize("tool,field,item", BATCHES, ids=[b[0] for b in BATCHES])
def test_one_card_covers_the_whole_batch(tool, field, item, db_isolated):
    """A bulk decision is ONE decision. Ten items must not become ten cards — the batch is a
    single tool call, so it stages a single card that lists what it covers."""
    _, proposed = call(tool, **{field: [item] * 3})
    assert proposed["display"]["count"] == 3
    assert len(proposed["display"]["rows"]) == 3


@pytest.mark.parametrize("tool,field,item", BATCHES, ids=[b[0] for b in BATCHES])
def test_an_unattended_caller_still_writes_without_a_card(
    tool, field, item, monkeypatch, db_isolated
):
    """The reason the hatch existed, served properly: nobody is watching, so nothing is
    staged — decided by the SURFACE, never by the caller's choice of argument."""
    monkeypatch.setattr(registry, "should_preview_write", lambda: False)
    text, proposed = call(tool, **{field: [item]})
    assert proposed is None, f"{tool} staged a card with no one to answer it"
    assert text is not None


def test_no_tool_advertises_a_way_around_the_gate():
    """The descriptions used to TEACH the bypass. A tool may say a batch is one call; it may
    never promise that a batch skips confirmation."""
    import backend.ai.tools  # noqa: F401  (populates the registry)
    from backend.ai.tools.registry import build_tool_schemas

    offenders = [
        s["name"]
        for s in build_tool_schemas()
        if "always applies immediately" in json.dumps(s).lower()
        or "no confirm card" in json.dumps(s).lower()
    ]
    assert not offenders, f"these tools advertise a gate bypass to the model: {offenders}"


@pytest.mark.parametrize("tool,field,item", BATCHES, ids=[b[0] for b in BATCHES])
def test_a_batch_of_one_never_writes_ungated(tool, field, item, db_isolated):
    """A one-item array is routed to the tool's SINGLE path (its real card, re-verified on
    approval). That path is gated too: it stages a card, or — for these placeholder ids that
    can't be previewed — refuses. It never writes."""
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore
    from backend.db.realized_event_store import RealizedEventStore

    before = (PositionStore().all(), LotStore().all(), RealizedEventStore().query_events())
    text, proposed = call(tool, **{field: [item]})
    after = (PositionStore().all(), LotStore().all(), RealizedEventStore().query_events())

    assert after == before, f"{tool} wrote on an attended one-item call"
    assert proposed is not None or "error" in (text or {}), (tool, text)
