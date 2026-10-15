"""An Automation may flag a transaction for review, never approve one.

The incident: an email-logging Automation (``permission_mode=auto``) created a row — born
``needs_review=True`` because ``automation`` is not a trusted writer — and 19 seconds later, in
the same run, sent ``update_transactions`` with ``needs_review=false`` bundled into a payee fix.
The review queue never saw it. The audit trail called that "Approved", by Lucius.

What this pins:
- the ledger update choke REFUSES a True→False flip under an Automation's audit scope, whatever
  the approval mode (it is not a tool permission), and the row stays flagged;
- the refusal reaches the agent as a message it can act on — what, why, what to do instead;
- everything short of that still works for the same run: other edits, re-flagging, a clear that
  changes nothing;
- the owner is unaffected on every other surface;
- a bulk change refuses atomically — no row is written before the refusal;
- the confirm-card preview refuses too, so a manual-approval run is never shown a card that can
  only fail once tapped;
- the create tool cannot hand an Automation a pre-approved row either.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools.ledger import (
    _preview_bulk_update,
    _preview_one_update,
    add_transactions,
    update_transactions,
)
from backend.core import audit_context
from backend.core.errors import ReviewApprovalRefusedError
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as svc


@pytest.fixture(autouse=True)
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    svc.accounts.insert({"id": "sgd", "name": "Meridian Bank", "kind": "asset", "currency": "SGD"})
    audit_context.clear_audit_scope()
    yield
    audit_context.clear_audit_scope()


def _as_automation() -> None:
    audit_context.set_audit_scope(
        actor="ai",
        surface="automation",
        session_id="automation-run-run_test",
        automation="Log transactions from emails",
        automation_id="task-test",
        automation_run_id="run_test",
    )


def _flagged_row(desc: str = "Kopitiam") -> dict:
    """A row exactly as an Automation books it: source=automation, no explicit review flag."""
    row = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-10-03",
            "description": desc,
            "from_account_id": "sgd",
            "from_amount": 15.4,
            "from_currency": "SGD",
            "source": "automation",
        }
    )
    assert svc.transactions.find_by_id(row["id"])["needs_review"] is True
    return row


def _stored(txn_id: str) -> dict:
    return svc.transactions.find_by_id(txn_id)


# ── The choke ────────────────────────────────────────────────────────────────────


def test_automation_cannot_clear_the_review_flag():
    row = _flagged_row()
    _as_automation()

    with pytest.raises(ReviewApprovalRefusedError):
        svc.update_transaction(row["id"], {"needs_review": False})

    assert _stored(row["id"])["needs_review"] is True


def test_a_none_value_is_a_clear_too():
    """The row builder coerces ``None`` to ``False`` — it must not be a way round the rule."""
    row = _flagged_row()
    _as_automation()

    with pytest.raises(ReviewApprovalRefusedError):
        svc.update_transaction(row["id"], {"needs_review": None})

    assert _stored(row["id"])["needs_review"] is True


def test_the_refusal_applies_nothing_from_the_same_patch():
    row = _flagged_row()
    _as_automation()

    with pytest.raises(ReviewApprovalRefusedError):
        svc.update_transaction(row["id"], {"needs_review": False, "memo": "sneaked in"})

    assert (_stored(row["id"]).get("memo") or "") == ""


def test_the_same_run_can_still_edit_and_reflag():
    row = _flagged_row()
    _as_automation()

    svc.update_transaction(row["id"], {"memo": "Mixed fruits"})
    assert _stored(row["id"])["memo"] == "Mixed fruits"
    assert _stored(row["id"])["needs_review"] is True

    svc.update_transaction(row["id"], {"needs_review": True})
    assert _stored(row["id"])["needs_review"] is True


def test_clearing_a_flag_the_row_does_not_carry_is_not_refused():
    row = _flagged_row()
    svc.update_transaction(row["id"], {"needs_review": False})  # the owner approves it
    _as_automation()

    svc.update_transaction(row["id"], {"needs_review": False, "memo": "still fine"})

    assert _stored(row["id"])["memo"] == "still fine"


@pytest.mark.parametrize("surface", ["ui", "chat:web", "chat:telegram", "mcp"])
def test_every_other_surface_can_still_approve(surface):
    row = _flagged_row()
    audit_context.set_audit_scope(actor="user", surface=surface)

    svc.update_transaction(row["id"], {"needs_review": False})

    assert _stored(row["id"])["needs_review"] is False


def test_with_no_scope_at_all_approving_still_works():
    row = _flagged_row()

    svc.update_transaction(row["id"], {"needs_review": False})

    assert _stored(row["id"])["needs_review"] is False


def test_the_message_tells_the_agent_what_why_and_what_instead():
    row = _flagged_row()
    _as_automation()

    with pytest.raises(ReviewApprovalRefusedError) as exc:
        svc.update_transaction(row["id"], {"needs_review": False})

    msg = str(exc.value)
    assert row["id"] in msg
    assert "cannot approve" in msg and "owner" in msg and "review queue" in msg
    assert "NOTHING in this call was applied" in msg
    assert "WITHOUT needs_review" in msg
    assert "not a failure" in msg
    assert exc.value.code == "review_approval_owner_only"


# ── Bulk ─────────────────────────────────────────────────────────────────────────


def test_bulk_clear_refuses_before_any_row_is_written():
    first, second = _flagged_row("First"), _flagged_row("Second")
    _as_automation()

    with pytest.raises(ReviewApprovalRefusedError):
        svc.bulk_update_transactions(
            [first["id"], second["id"]], {"needs_review": False, "memo": "batch"}
        )

    for row in (first, second):
        assert _stored(row["id"])["needs_review"] is True
        assert (_stored(row["id"]).get("memo") or "") == ""


def test_owner_bulk_approve_still_works():
    first, second = _flagged_row("First"), _flagged_row("Second")

    result = svc.bulk_update_transactions([first["id"], second["id"]], {"needs_review": False})

    assert result["count"] == 2
    assert _stored(first["id"])["needs_review"] is False


# ── What the agent actually receives, through the tool ──────────────────────────


@pytest.mark.asyncio
async def test_single_update_tool_returns_the_refusal_as_the_error():
    row = _flagged_row()
    _as_automation()

    out, _ = await update_transactions(
        {"updates": [{"transaction_id": row["id"], "memo": "x", "needs_review": False}]}
    )
    body = json.loads(out)

    assert body["ok"] is False
    assert "cannot approve" in body["error"] and "WITHOUT needs_review" in body["error"]
    assert _stored(row["id"])["needs_review"] is True
    assert (_stored(row["id"]).get("memo") or "") == ""


@pytest.mark.asyncio
async def test_batch_update_refuses_the_flagged_rows_and_applies_the_rest():
    """The incident's exact shape: several rows, each carrying ``needs_review=false``, one with
    a legitimate edit riding along. The clean row must still land; the refused rows must say why,
    per row, where the agent reads results."""
    refused, clean = _flagged_row("Refused"), _flagged_row("Clean")
    _as_automation()

    out, _ = await update_transactions(
        {
            "updates": [
                {"transaction_id": refused["id"], "memo": "x", "needs_review": False},
                {"transaction_id": clean["id"], "memo": "applied"},
            ]
        }
    )
    body = json.loads(out)

    assert body["updated"] == 1
    by_id = {r["transaction_id"]: r for r in body["results"]}
    assert "cannot approve" in by_id[refused["id"]]["error"]
    assert by_id[clean["id"]]["ok"] is True
    assert _stored(refused["id"])["needs_review"] is True
    assert _stored(clean["id"])["memo"] == "applied"


@pytest.mark.asyncio
async def test_resending_without_the_flag_succeeds():
    """The message's own instruction must work: same edit, minus ``needs_review``."""
    row = _flagged_row()
    _as_automation()

    out, _ = await update_transactions(
        {"updates": [{"transaction_id": row["id"], "memo": "Mixed fruits"}]}
    )

    assert json.loads(out)["ok"] is True
    assert _stored(row["id"])["memo"] == "Mixed fruits"
    assert _stored(row["id"])["needs_review"] is True


# ── The confirm-card preview ─────────────────────────────────────────────────────


def test_preview_refuses_so_no_card_is_staged_for_a_write_that_would_fail():
    row = _flagged_row()
    _as_automation()

    change = _preview_one_update(row["id"], {"needs_review": False}, None)

    assert "cannot approve" in change["error"]


def test_bulk_preview_surfaces_the_refusal_per_row():
    row = _flagged_row()
    _as_automation()

    change = _preview_bulk_update([{"transaction_id": row["id"], "needs_review": False}])

    assert change["errors"] and "cannot approve" in json.dumps(change["errors"])


def test_preview_still_shows_a_reflag_for_the_owner():
    row = _flagged_row()
    svc.update_transaction(row["id"], {"needs_review": False})

    change = _preview_one_update(row["id"], {"needs_review": True}, None)

    assert not change.get("error")
    assert any(f["field"] == "needs_review" for f in change["fields"])


# ── No way round it at create ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_tool_cannot_hand_an_automation_a_pre_approved_row():
    _as_automation()

    out, _ = await add_transactions(
        {
            "transactions": [
                {
                    "date": "2026-10-03",
                    "description": "Kopitiam",
                    "amount": 4.5,
                    "currency": "SGD",
                    "account_id": "sgd",
                    "needs_review": False,
                }
            ]
        }
    )
    body = json.loads(out)

    assert body["ok"] is True, body
    assert _stored(body["transaction"]["id"])["needs_review"] is True
