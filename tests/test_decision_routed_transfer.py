"""A review decision may route a statement row as a transfer to another of the owner's accounts
(``edits: {to_account_id}``) — booked in the same import batch through the ledger choke, so the
transfer's two legs, dedup identity and rollback behave like any other row of the batch.

LLM stubbed via the seam; stores isolated (mirrors test_decision_import.py's env fixture)."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools.statements import rollback_import_batch
from backend.core import fx as core_fx
from backend.core import llm_seam
from backend.core.errors import ValidationError
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc
from backend.services.statements.tiers import StatementParseError


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.core.paths as core_paths_mod
    import backend.db.statement_store as stmt_mod

    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    files_dir = tmp_path / "statements"
    files_dir.mkdir()
    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", files_dir)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(core_fx, "_fx_rates_loader", lambda: {"SGD": 1.0, "USD": 1.35})
    monkeypatch.setattr(llm_seam, "_parse_json", None)
    for acct in (
        {"id": "acc", "name": "Meridian Bank Card", "kind": "liability", "currency": "SGD"},
        {"id": "sav", "name": "Meridian Savings", "kind": "asset", "currency": "SGD"},
        {"id": "usd", "name": "Harbor USD", "kind": "asset", "currency": "USD"},
        {"id": "thb", "name": "Siam THB", "kind": "asset", "currency": "THB"},
    ):
        ledger_service.accounts.insert(acct)


_ROWS = [
    {
        "date": "2026-05-21",
        "amount": 60.0,
        "direction": "OUT",
        "payee": "Cinebar",
        "description": "MPOS*CINEBAR 60.00",
        "reference": "74720746142802720528652",
        "confidence": 0.97,
    },
    {
        "date": "2026-05-22",
        "amount": 200.0,
        "direction": "OUT",
        "payee": None,
        "description": "TRANSFER TO SAVINGS",
        "confidence": 0.97,
    },
    {
        "date": "2026-05-23",
        "amount": 135.0,
        "direction": "IN",
        "payee": None,
        "description": "TRANSFER FROM HARBOR",
        "confidence": 0.97,
    },
]
_META = {
    "currency": "SGD",
    "sign_convention": "card",
    "issuer": "Meridian Bank",
    "opening_balance": 0.0,
    "closing_balance": 125.0,
    "account_last4s": ["1111"],
}


def _make_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


_PDF = _make_pdf(
    "Meridian Bank\nMPOS*CINEBAR 60.00\nTRANSFER TO SAVINGS 200.00\nTRANSFER FROM HARBOR"
)


def _preview(monkeypatch) -> dict:
    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            return json.dumps(_META)
        return json.dumps(_ROWS)

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    return svc.decision_preview(_PDF, "pdf", "acc", filename="may.pdf")


def _live() -> list[dict]:
    return [t for t in ledger_service.transactions.all() if not t.get("deleted")]


def _route(row: int, to: str, **extra) -> dict:
    return {"row": row, "action": "create", "edits": {"to_account_id": to, **extra}}


def test_routed_row_books_one_two_leg_transfer_in_the_batch(monkeypatch):
    res = _preview(monkeypatch)
    out = svc.decision_commit(
        res["statement_id"], "acc", decisions=[_route(1, "Meridian Savings")], default_action="skip"
    )
    assert out["created"] == 1 and out["transfers"] == 1
    (txn,) = _live()
    assert txn["kind"] == "transfer" and txn["batch_id"] == out["batch_id"]
    assert (txn["from_account_id"], txn["from_amount"]) == ("acc", 200.0)
    assert (txn["to_account_id"], txn["to_amount"]) == ("sav", 200.0)
    assert txn["cleared"] is True


def test_inbound_row_routes_the_other_account_as_the_source(monkeypatch):
    res = _preview(monkeypatch)
    svc.decision_commit(
        res["statement_id"], "acc", decisions=[_route(2, "usd")], default_action="skip"
    )
    (txn,) = _live()
    assert (txn["from_account_id"], txn["to_account_id"]) == ("usd", "acc")
    assert txn["to_amount"] == 135.0  # the statement's figure stays on the statement's leg
    assert txn["from_currency"] == "USD" and txn["from_amount"] == pytest.approx(100.0)


def test_cross_currency_leg_is_derived_not_relabelled(monkeypatch):
    res = _preview(monkeypatch)
    svc.decision_commit(
        res["statement_id"], "acc", decisions=[_route(1, "usd")], default_action="skip"
    )
    (txn,) = _live()
    assert (txn["from_amount"], txn["from_currency"]) == (200.0, "SGD")
    assert txn["to_currency"] == "USD"
    assert txn["to_amount"] == pytest.approx(200.0 / 1.35, abs=0.01)


def test_rollback_removes_the_routed_transfer_with_the_batch(monkeypatch):
    res = _preview(monkeypatch)
    out = svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[_route(1, "sav"), {"row": 0, "action": "create"}],
        default_action="skip",
    )
    assert out["created"] == 2 and len(_live()) == 2
    reply, _ = asyncio.run(rollback_import_batch({"batch_id": out["batch_id"], "confirm": True}))
    assert json.loads(reply)["deleted"] == 2
    assert _live() == []


_ONLY_ROW_1 = [{"row": 0, "action": "skip"}, {"row": 2, "action": "skip"}]


def test_reimport_dedups_the_routed_transfer(monkeypatch):
    res = _preview(monkeypatch)
    out = svc.decision_commit(
        res["statement_id"], "acc", decisions=[_route(1, "sav")], default_action="skip"
    )
    assert out["created"] == 1
    fresh = _preview(monkeypatch)
    again = svc.decision_commit(fresh["statement_id"], "acc", decisions=_ONLY_ROW_1)
    assert again["created"] == 0 and again["duplicates"] == 1
    assert len(_live()) == 1


@pytest.mark.parametrize(
    "target, message",
    [
        ("Nowhere Fund", "not an account"),
        ("acc", "two different accounts"),
        ("meridian bank card", "two different accounts"),
        ("Siam THB", "no exchange rate for SGD"),
    ],
)
def test_unusable_destinations_are_refused_and_nothing_is_written(monkeypatch, target, message):
    res = _preview(monkeypatch)
    with pytest.raises(StatementParseError, match=message):
        svc.decision_commit(
            res["statement_id"], "acc", decisions=[_route(1, target)], default_action="skip"
        )
    assert _live() == []


def test_archived_destination_is_refused(monkeypatch):
    ledger_service.accounts.update_by_id("sav", {"archived": True})
    res = _preview(monkeypatch)
    with pytest.raises(StatementParseError, match="archived"):
        svc.decision_commit(res["statement_id"], "acc", decisions=[_route(1, "sav")])


def test_ambiguous_destination_name_names_every_candidate(monkeypatch):
    ledger_service.accounts.insert(
        {"id": "sav2", "name": "Meridian Savings", "kind": "asset", "currency": "SGD"}
    )
    res = _preview(monkeypatch)
    with pytest.raises(ValidationError) as exc:
        svc.decision_commit(res["statement_id"], "acc", decisions=[_route(1, "Meridian Savings")])
    assert "sav" in str(exc.value) and "sav2" in str(exc.value)


def test_kind_must_be_transfer_and_needs_a_destination(monkeypatch):
    res = _preview(monkeypatch)
    for edits, message in (
        ({"kind": "income", "to_account_id": "sav"}, "can only be 'transfer'"),
        ({"kind": "transfer"}, "needs edits.to_account_id"),
    ):
        with pytest.raises(StatementParseError, match=message):
            svc.decision_commit(
                res["statement_id"],
                "acc",
                decisions=[{"row": 1, "action": "create", "edits": edits}],
            )
    svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[
            {"row": 1, "action": "create", "edits": {"kind": "Transfer", "to_account_id": "sav"}}
        ],
        default_action="skip",
    )
    assert [t["kind"] for t in _live()] == ["transfer"]


def test_a_merge_cannot_also_route_as_a_transfer(monkeypatch):
    res = _preview(monkeypatch)
    decision = {"row": 1, "action": "merge", "existing_id": "x", "edits": {"to_account_id": "sav"}}
    with pytest.raises(StatementParseError, match="can't also route"):
        svc.decision_commit(res["statement_id"], "acc", decisions=[decision])
