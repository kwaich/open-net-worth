"""Phase 1 of the transaction-provenance / audit-trail feature.

Covers the store CRUD, the field diff, the scope-aware recorder, the four ledger
emission points (create / bulk-create / update / bulk-delete), the inner-create
SUPPRESSION (an edit emits exactly one "update", never a phantom "create"), and the
trace() read path with its field-derived forensic fallback for un-audited rows.

DESIGN: .claude/rfcs/transaction-provenance.md
"""

from __future__ import annotations

import pytest

from backend.core import audit_context
from backend.db.category_store import CategoryStore
from backend.db.transaction_audit_store import TransactionAuditStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import audit
from backend.services import ledger as svc


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions/audit are DB-backed now (plan 3 task 4); categories are
    # DB-backed too (plan 4a task 2); payees are DB-backed too (plan 4d task 2) —
    # db_isolated redirects every store instance (svc.transactions/.accounts,
    # audit._audit/._txns, payee_registry._store) to the SAME fresh, empty database, so
    # audit._txns and the ledger's transactions already share storage without any
    # per-instance path-sync. Reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared
    # autouse seed ran BEFORE db_isolated activated, so it landed in the old, now-orphaned
    # default engine).
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    svc.accounts.insert({"id": "acc_sav", "name": "Savings", "kind": "asset", "currency": "SGD"})
    audit_context.clear_audit_scope()
    yield tmp_path
    audit_context.clear_audit_scope()


def _expense(desc="Anthropic, PBC", category=None, amount=11.5, date="2026-05-31"):
    return {
        "kind": "expense",
        "date": date,
        "description": desc,
        "category": category,
        "from_account_id": "acc_chk",
        "from_amount": amount,
        "from_currency": "SGD",
    }


# ── changed_fields (the diff) ────────────────────────────────────────────────────
def test_changed_fields_none_inputs_return_none():
    assert audit.changed_fields(None, {"a": 1}) == (None, None)
    assert audit.changed_fields({"a": 1}, None) == (None, None)


def test_changed_fields_returns_only_changed_keys():
    before = {"date": "2024-09-07", "amount": 1, "x": 5}
    after = {"date": "2026-06-07", "amount": 1, "x": 5}
    b, a = audit.changed_fields(before, after)
    assert b == {"date": "2024-09-07"}
    assert a == {"date": "2026-06-07"}


def test_changed_fields_ignores_volatile_keys():
    before = {"created_at": "t1", "date": "d"}
    after = {"created_at": "t2", "date": "d"}
    assert audit.changed_fields(before, after) == (None, None)


def test_changed_fields_no_change_returns_none():
    assert audit.changed_fields({"a": 1, "b": 2}, {"a": 1, "b": 2}) == (None, None)


def test_changed_fields_treats_none_and_empty_string_as_equivalent():
    """A legacy pre-split row's memo silently normalizes None -> '' on its first
    TransactionStore rebuild (make_transaction) — both render as '—' in the UI, so this
    must NOT surface as a spurious diff on a row's first edit, real or not."""
    before = {"memo": None, "amount": 1}
    after = {"memo": "", "amount": 1}
    assert audit.changed_fields(before, after) == (None, None)
    # And the mirror direction.
    assert audit.changed_fields({"memo": ""}, {"memo": None}) == (None, None)


def test_changed_fields_none_to_empty_string_does_not_hide_a_real_sibling_change():
    """The None<->'' collapse is per-field, not a reason to drop the whole event — a real
    change on another field in the SAME update still surfaces."""
    before = {"memo": None, "description": "Coffee Shop"}
    after = {"memo": "", "description": "Starbucks"}
    b, a = audit.changed_fields(before, after)
    assert b == {"description": "Coffee Shop"}
    assert a == {"description": "Starbucks"}


def test_changed_fields_none_to_zero_is_still_a_real_change():
    """The collapse is narrowly None<->'' — None<->0 (or any other falsy-but-distinct
    value) is a genuine change and must still be reported."""
    before = {"cost_basis": None}
    after = {"cost_basis": 0}
    b, a = audit.changed_fields(before, after)
    assert b == {"cost_basis": None}
    assert a == {"cost_basis": 0}


# ── store CRUD ───────────────────────────────────────────────────────────────────
def test_store_append_and_for_txn_orders_by_ts(env):
    store = TransactionAuditStore()
    store.append({"id": "e2", "txn_id": "t1", "op": "update", "ts": "2026-06-08T02:00:00Z"})
    store.append({"id": "e1", "txn_id": "t1", "op": "create", "ts": "2026-06-07T01:00:00Z"})
    store.append({"id": "e3", "txn_id": "OTHER", "op": "create", "ts": "2026-06-09T00:00:00Z"})
    events = store.for_txn("t1")
    assert [e["id"] for e in events] == ["e1", "e2"]  # ascending ts; OTHER excluded
    assert store.has_any_for("t1") is True
    assert store.has_any_for("nope") is False


def test_store_append_many(env):
    store = TransactionAuditStore()
    assert store.append_many([]) == []
    store.append_many(
        [
            {"id": "a", "txn_id": "t", "op": "create", "ts": "2026-01-01T00:00:00Z"},
            {"id": "b", "txn_id": "t", "op": "delete", "ts": "2026-01-02T00:00:00Z"},
        ]
    )
    assert len(store.for_txn("t")) == 2


# ── recorder (scope-aware) ───────────────────────────────────────────────────────
def test_record_event_captures_active_scope(env):
    audit_context.set_audit_scope(
        "ai", "chat:web", session_id="sess1", sender_id=None, intent="add them in"
    )
    ev = audit.record_txn_event("create", "txnX", source="ai")
    assert ev is not None
    assert ev["actor"] == "ai"
    assert ev["surface"] == "chat:web"
    assert ev["session_id"] == "sess1"
    assert ev["intent"] == "add them in"
    assert ev["source"] == "ai"
    assert ev["op"] == "create"


def test_record_event_without_scope_falls_back(env):
    ev = audit.record_txn_event("create", "txnY")
    assert ev["actor"] == "system"
    assert ev["surface"] == "unknown"
    assert ev["session_id"] is None


def test_record_event_unknown_op_skipped(env):
    assert audit.record_txn_event("frobnicate", "txnZ") is None


# ── ledger emission points ───────────────────────────────────────────────────────
def test_create_transaction_emits_one_create_event(env):
    row = svc.create_transaction(_expense())
    events = audit._audit.for_txn(row["id"])
    assert len(events) == 1
    assert events[0]["op"] == "create"
    assert events[0]["source"] == row["source"]


def test_update_transaction_emits_single_update_with_diff(env):
    row = svc.create_transaction(_expense(date="2024-09-07"))
    svc.update_transaction(row["id"], {"date": "2026-06-07"})
    events = audit._audit.for_txn(row["id"])
    # Exactly ONE create (suppression worked) + ONE update — never create+create+update.
    assert [e["op"] for e in events] == ["create", "update"]
    upd = events[1]
    assert upd["before"] == {"date": "2024-09-07"}
    assert upd["after"] == {"date": "2026-06-07"}


def test_update_transaction_noop_emits_no_update_event(env):
    row = svc.create_transaction(_expense(category="subscriptions"))
    # Re-set the same values → no meaningful field change → no "update" event.
    svc.update_transaction(row["id"], {"category": "subscriptions"})
    events = audit._audit.for_txn(row["id"])
    assert [e["op"] for e in events] == ["create"]


def test_bulk_create_emits_one_create_per_row(env):
    created, _ = svc.bulk_create_transactions([_expense(desc="A"), _expense(desc="B")])
    for c in created:
        events = audit._audit.for_txn(c["id"])
        assert [e["op"] for e in events] == ["create"]


def test_bulk_delete_emits_delete_per_row(env):
    a = svc.create_transaction(_expense(desc="A"))
    b = svc.create_transaction(_expense(desc="B"))
    svc.bulk_delete_transactions([a["id"], b["id"]])
    for tid in (a["id"], b["id"]):
        ops = [e["op"] for e in audit._audit.for_txn(tid)]
        assert ops == ["create", "delete"]


# ── trace + forensic fallback ────────────────────────────────────────────────────
def test_trace_returns_recorded_events(env):
    row = svc.create_transaction(_expense())
    result = audit.trace(row["id"])
    assert result["reconstructed"] is False
    assert len(result["events"]) >= 1


def test_trace_reconstructs_when_no_recorded_events(env):
    # Insert a row directly (bypassing the choke point) so NO audit event exists.
    svc.transactions.insert(
        {"id": "legacy1", "kind": "expense", "source": "ai", "created_at": "2026-01-02T03:04:05Z"}
    )
    result = audit.trace("legacy1")
    assert result["reconstructed"] is True
    assert len(result["events"]) == 1
    ev = result["events"][0]
    assert ev["op"] == "create"
    assert ev["actor"] == "ai"
    assert ev["surface"] == "chat:web"
    assert ev["ts"] == "2026-01-02T03:04:05Z"
    assert "reconstructed" in (ev["note"] or "")


def test_reconstruct_infers_import_surface(env):
    svc.transactions.insert({"id": "imp1", "kind": "expense", "source": "import", "batch_id": "b1"})
    ev = audit.reconstruct("imp1")[0]
    assert ev["actor"] == "user"
    assert ev["surface"] == "import"


def test_reconstruct_unknown_txn_returns_empty(env):
    assert audit.reconstruct("does-not-exist") == []
    result = audit.trace("does-not-exist")
    assert result["events"] == []


# ── "Added by" agent-name resolution (drawer provenance) ──────────────────────
# Regression: an agent-authored row showed the generic "Agent" in the drawer's
# "Added by" instead of the agent's real name (e.g. "Transaction & Trade Logger").
# activity().addedBy must resolve the agent:<id> surface to the agent's display name.
def test_added_by_recurring_surface_is_schedule():
    ev = {"op": "create", "surface": "recurring:vest_reminder"}
    assert audit._added_by_name(ev, "automation") == "Schedule"


def test_added_by_non_agent_uses_source_label():
    assert audit._added_by_name({"op": "create", "surface": "ui"}, "manual") == "You"
    assert audit._added_by_name(None, "bank") == "Bank feed"


# ── Timeline actor resolution (drawer Activity rows) ──────────────────────────
# Regression: every agent row showed the generic id "agent" plus a redundant
# "via <agent-slug>"; the actor must resolve to the agent's display name with no
# slug-via, and the note (which records the agent name) must not be duplicated.
def test_timeline_actor_lucius_user_system():
    assert audit._timeline_actor({"actor": "ai", "surface": "chat:web"}) == ("lucius", "Lucius")
    assert audit._timeline_actor({"actor": "user", "surface": "ui"}) == ("you", "You")
    assert audit._timeline_actor({"actor": "system", "surface": "unknown"}) == ("system", "System")
    assert audit._timeline_actor({"actor": "system", "surface": "recurring:t1"}) == (
        "automation",
        "Schedule",
    )


def test_event_via_drops_agent_slug_keeps_meaningful():
    assert audit._event_via({"surface": "agent:ttl"}, "Transaction & Trade Logger") is None
    assert audit._event_via({"surface": "recurring:t1"}, "Schedule") is None
    assert audit._event_via({"surface": "recurring:t1"}, "Some Agent") == "Schedule"


# ── Automation provenance ("via <automation>") + chat channel via ─────────────
# Capture richly: the audit event records the firing automation's id AND a title
# snapshot; the activity view renders the title as "via <X>" and exposes the id.
def test_audit_event_captures_automation_id_and_title(env):
    audit_context.set_audit_scope(
        actor="agent",
        surface="agent:ttl",
        note="Transaction & Trade Logger",
        automation="Weekly Fraud Checking",
        automation_id="task_abc",
    )
    row = svc.create_transaction({**_expense(desc="KestrelEats"), "source": "agent"})
    audit_context.clear_audit_scope()
    ev = audit._audit.for_txn(row["id"])[0]
    assert ev["automation"] == "Weekly Fraud Checking"
    assert ev["automation_id"] == "task_abc"


def test_via_for_chat_channel_and_none_for_web(env):
    # A non-web chat channel surfaces as "via Telegram"; web chat / direct UI show no via.
    assert audit._event_via({"surface": "chat:telegram"}, "Lucius") == "Telegram"
    assert audit._event_via({"surface": "chat:slack"}, "Lucius") == "Slack"
    assert audit._event_via({"surface": "chat:web"}, "Lucius") is None
    assert audit._event_via({"surface": "ui"}, "You") is None


def test_automation_via_wins_over_channel_and_skips_when_equal_to_actor(env):
    # The named automation takes priority; but if it equals the actor name it's redundant.
    assert (
        audit._event_via(
            {"surface": "agent:x", "automation": "Weekly Fraud Checking"}, "Fraud Detection Agent"
        )
        == "Weekly Fraud Checking"
    )
    assert audit._event_via({"surface": "agent:x", "automation": "Logger"}, "Logger") is None


# ── Review-decision verbs (approve / reject / flag) ──────────────────────────────
# Display-only refinement in activity() — storage is untouched (the flag flip IS the
# stored truth), so these verbs apply retroactively to already-recorded decisions.
def test_activity_maps_review_clear_to_approved(env):
    row = svc.create_transaction({**_expense(), "needs_review": True})
    svc.update_transaction(row["id"], {"needs_review": False})
    acts = audit.activity(row["id"])["activity"]
    assert [a["action"] for a in acts] == ["created", "approved"]
    # The flag flip is the decision itself, not a user-facing field edit.
    assert "changes" not in acts[1]


def test_activity_maps_review_set_to_flagged(env):
    row = svc.create_transaction(_expense())
    svc.update_transaction(row["id"], {"needs_review": True})
    acts = audit.activity(row["id"])["activity"]
    assert acts[-1]["action"] == "flagged"
    assert "changes" not in acts[-1]


def test_activity_approve_alongside_other_edits_keeps_their_diff(env):
    row = svc.create_transaction({**_expense(date="2024-09-07"), "needs_review": True})
    svc.update_transaction(row["id"], {"needs_review": False, "date": "2026-06-07"})
    ev = audit.activity(row["id"])["activity"][-1]
    assert ev["action"] == "approved"
    fields = {c["field"] for c in ev.get("changes", [])}
    assert "date" in fields
    assert "needs_review" not in fields


def test_activity_maps_delete_of_flagged_row_to_rejected(env):
    row = svc.create_transaction({**_expense(), "needs_review": True})
    svc.bulk_delete_transactions([row["id"]])
    acts = audit.activity(row["id"])["activity"]
    assert [a["action"] for a in acts] == ["created", "rejected"]


def test_activity_delete_after_approve_stays_deleted(env):
    row = svc.create_transaction({**_expense(), "needs_review": True})
    svc.update_transaction(row["id"], {"needs_review": False})
    svc.bulk_delete_transactions([row["id"]])
    acts = audit.activity(row["id"])["activity"]
    assert acts[-1]["action"] == "deleted"


def test_activity_plain_update_still_reads_updated(env):
    row = svc.create_transaction(_expense(date="2024-09-07"))
    svc.update_transaction(row["id"], {"date": "2026-06-07"})
    acts = audit.activity(row["id"])["activity"]
    assert acts[-1]["action"] == "updated"
