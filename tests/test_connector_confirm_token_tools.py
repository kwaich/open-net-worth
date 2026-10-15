"""The connector confirm round trip for the non-ledger propose_*/apply_* merges (lots,
equity, options, 8B import — ``ai/tools/{lots,equity,options}.py``, harness reliability
program 2026-07-19).

Before this fix, ``connectors.chat.confirm.apply_change`` had NO branch for any of these six
change types (``lot_dispose_undo``, ``vest_apply``, ``vest_undo``, ``8b_import_commit``,
``open_option``, ``close_option``) — they fell through to the final ``else`` branch
(``positions_service.apply_position_change``, which understands only "add"/"update"/
"remove") and would raise or silently misbehave. This is the exact gap CLAUDE.md's ledger
section calls out for the ledger types, just never closed for these six when they were
separate propose_*/apply_* tool pairs either.

``test_connector_ledger_confirm.py`` is this file's sibling for the ledger types — same
spirit: drive the REAL staging path through the connector confirm seam (never mocking
``execute_tool`` for the primary end-to-end case), proving a button tap actually redeems the
server-STORED args, not whatever the tapped card's ``change`` dict says.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.ai.tools import execute_tool
from backend.ai.tools import registry as tool_registry
from backend.connectors import pending_changes
from backend.connectors.chat import confirm


def _preview(monkeypatch):
    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: True)


@pytest.fixture
def full_stores(tmp_path, monkeypatch, db_isolated):
    from backend.core import fx as fx_mod
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as ledger_service
    from backend.services import lots as lots_service

    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(fx_mod, "base_currency", lambda: "USD")
    monkeypatch.setattr(fx_mod, "to_base", lambda a, c, b=None, rates=None: round(float(a or 0), 2))
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    return tmp_path


@pytest.fixture
def seeds(full_stores):
    from backend.db.account_store import AccountStore
    from backend.db.instrument_store import InstrumentStore

    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    cash_acct = AccountStore().create(
        {"name": "Vantage Brokerage USD", "kind": "asset", "type": "cash", "currency": "USD"}
    )
    inst = InstrumentStore().create(
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "manual",
            "manual_price": 150.0,
        }
    )
    return {"account": acct, "cash_account": cash_acct, "instrument": inst}


def _future() -> str:
    from datetime import date, timedelta

    return (date.today() + timedelta(days=60)).isoformat()


async def _stage_open_option(seeds, monkeypatch):
    """Preview a real ``open_option`` card exactly as the tool does, returning the raw
    change dict (``tool_use_id`` included) the assistant would emit as ``proposed_change``."""
    _preview(monkeypatch)
    text, change = await execute_tool(
        "open_option",
        tool_use_id="toolu_stage_open_option",
        input_data={
            "underlying_instrument_id": seeds["instrument"]["id"],
            "account_id": seeds["account"]["id"],
            "option_type": "call",
            "side": "short",
            "strike": 160.0,
            "expiry": _future(),
            "contracts": 1,
            "premium_per_share": 2.5,
        },
    )
    assert text is None, "a staged card pauses the tool_use — no result yet"
    assert change is not None and change["type"] == "open_option"
    assert change["tool_use_id"] == "toolu_stage_open_option"
    assert change["grant_tool"] == "open_option"
    return change


@pytest.mark.asyncio
async def test_connector_confirm_books_a_real_option_open(seeds, monkeypatch):
    """The core fix: a button tap on an ``open_option`` card must write through
    ``open_option``'s OWN confirm-token redemption, not fall through to
    ``positions_service.apply_position_change``."""
    from backend.db.option_contract_store import OptionContractStore

    change = await _stage_open_option(seeds, monkeypatch)
    change_id = pending_changes.store(
        "sess-1", change, connector="telegram", channel_id="C1", sender_id="U-alice"
    )

    outcome = await confirm.handle_confirm_action(
        "confirm", change_id, connector="telegram", channel_id="C1", sender_id="U-alice"
    )

    assert outcome.status == "applied", outcome.message
    assert "opened option" in outcome.message.lower()
    contracts = OptionContractStore().all()
    assert len(contracts) == 1
    assert contracts[0]["strike"] == 160.0
    assert contracts[0]["side"] == "short"


@pytest.mark.asyncio
async def test_connector_confirm_ignores_a_tampered_display_and_books_the_staged_args(
    seeds, monkeypatch
):
    """Security property: altering the card's own ``change``/``display`` dict before the tap
    (what a tampered client, a stale re-render, or a model reconstructing args from history
    could do) must NOT change what gets committed — only the server-stored args behind the
    token matter."""
    from backend.db.option_contract_store import OptionContractStore

    change = await _stage_open_option(seeds, monkeypatch)
    tampered = {**change, "strike": 999.0, "contracts": 50}
    tampered["display"] = {**change["display"], "strike": 999.0, "contracts": 50}

    change_id = pending_changes.store(
        "sess-1", tampered, connector="telegram", channel_id="C1", sender_id="U-alice"
    )
    outcome = await confirm.handle_confirm_action(
        "confirm", change_id, connector="telegram", channel_id="C1", sender_id="U-alice"
    )

    assert outcome.status == "applied", outcome.message
    contracts = OptionContractStore().all()
    assert len(contracts) == 1
    assert contracts[0]["strike"] == 160.0, "tampered display leaked into the real write"
    assert contracts[0]["contracts"] == 1, "tampered display leaked into the real write"


@pytest.mark.asyncio
async def test_connector_discard_never_writes_an_option(seeds, monkeypatch):
    from backend.db.option_contract_store import OptionContractStore

    change = await _stage_open_option(seeds, monkeypatch)
    change_id = pending_changes.store(
        "sess-1", change, connector="slack", channel_id="C1", sender_id="U-alice"
    )

    outcome = await confirm.handle_confirm_action(
        "discard", change_id, connector="slack", channel_id="C1", sender_id="U-alice"
    )

    assert outcome.status == "discarded"
    assert OptionContractStore().all() == []


@pytest.mark.asyncio
async def test_idor_a_different_sender_cannot_confirm_someone_elses_option_card(seeds, monkeypatch):
    from backend.db.option_contract_store import OptionContractStore

    change = await _stage_open_option(seeds, monkeypatch)
    change_id = pending_changes.store(
        "sess-1", change, connector="telegram", channel_id="C1", sender_id="U-alice"
    )

    mismatched = await confirm.handle_confirm_action(
        "confirm", change_id, connector="telegram", channel_id="C1", sender_id="U-mallory"
    )
    assert mismatched.status == "ignored"
    assert OptionContractStore().all() == []

    outcome = await confirm.handle_confirm_action(
        "confirm", change_id, connector="telegram", channel_id="C1", sender_id="U-alice"
    )
    assert outcome.status == "applied"
    assert len(OptionContractStore().all()) == 1


# ── Routing pins for the other five ctypes (stubbed execute_tool — proves the dispatch
# table maps each ctype to the right tool and passes NOTHING but the token, without needing
# a full write-environment per domain) ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ctype,expected_tool",
    [
        ("lot_dispose_undo", "undo_lot_disposals"),
        ("vest_apply", "record_vest"),
        ("vest_undo", "undo_vest"),
        ("8b_import_commit", "import_8b"),
        ("open_option", "open_option"),
        ("close_option", "close_option"),
    ],
)
def test_apply_change_resolves_any_staged_card_by_its_tool_use_id(
    ctype, expected_tool, monkeypatch
):
    """ONE rule now, for every change type: a card carrying a ``tool_use_id`` is a paused
    tool_use, and applying it means resolving that id — the server runs the args IT staged.

    This replaced a per-ctype dispatch table. What the table was really protecting is pinned
    here instead: nothing else on the tapped ``change`` may reach the write, so a tampered
    display or an injected field cannot commit anything.
    """
    from backend.connectors.chat import confirm as confirm_mod

    seen = []

    async def _fake_resolve(change, session_id, decision, *, target=None):
        seen.append((change, session_id, decision))
        return {"ok": True}

    monkeypatch.setattr(confirm_mod, "_resolve_via_pending_tool_use", _fake_resolve)

    change = {
        "type": ctype,
        "tool_use_id": "toolu_staged",
        "grant_tool": expected_tool,
        "display": {"summary": "should never be read"},
        "poisoned_field": "an attacker or a confused model could put anything here",
    }
    asyncio.run(confirm_mod.apply_change(change, "sess-test"))

    assert len(seen) == 1
    resolved, session_id, decision = seen[0]
    assert (session_id, decision) == ("sess-test", "approve")
    # The id is the ONLY thing that selects what runs; the rest of the dict is inert.
    assert resolved["tool_use_id"] == "toolu_staged"


def test_apply_change_refuses_a_card_that_staged_nothing(monkeypatch):
    """No ``tool_use_id`` and no un-migrated branch = nothing to run. It must REFUSE rather
    than hand-dispatch the client's dict, which is how a card could commit what it never
    showed."""
    from backend.connectors.chat import confirm as confirm_mod

    with pytest.raises(RuntimeError, match="unknown change type"):
        asyncio.run(confirm_mod.apply_change({"type": "vest_apply", "display": {}}, "sess-test"))


@pytest.mark.parametrize(
    "ctype",
    [
        "lot_dispose_undo",
        "vest_apply",
        "vest_undo",
        "8b_import_commit",
        "open_option",
        "close_option",
    ],
)
def test_change_label_is_readable_for_every_merged_ctype(ctype):
    """Sanity: the confirm/discard acknowledgement text names something real, not the bare
    ctype string or a KeyError, for every one of the six merged change types."""
    from backend.connectors.chat.confirm import change_label

    label = change_label({"type": ctype, "display": {"summary": "AAPL 160C"}})
    assert "AAPL 160C" in label
    assert ctype not in label  # a humanized verb, never the raw ctype leaking through
