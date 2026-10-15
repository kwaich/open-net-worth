"""Load-bearing idempotency tests for email-sourced ledger writes.

SECURITY REGRESSION (CRITICAL): the email re-scan window re-presents fetched-but-not-logged
emails on the next run. The original safety premise — "bulk_create_transactions deduplicates
on external_id" — was FALSE for the agent's path: the email-transaction-logger writes via
``add_transaction`` (→ ``create_transaction``), which had NO external_id handling at all. So
re-presenting an email relied solely on the LLM not re-logging — unsafe for real money.

The fix makes double-logging mechanically impossible at the LEDGER:
  1. ``add_transaction`` accepts optional ``external_source``/``external_ref`` (carried through
     ``_txn_data_from_ai``).
  2. ``create_transaction`` does a ``transaction_store.has_external_ref(source, ref)`` check — an
     existing pair is a SAFE NO-OP returning the existing row, never a second insert.

These tests exercise the REAL ledger write path twice with the same email's external ref and
assert exactly ONE transaction row — the no-dupe property actually exercised, not asserted on
a false premise.

Offline + isolated: stores live in tmp_path; ``execute_tool`` is async, driven via
asyncio.run. No LLM, no network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.core.txn_sources import primary_pair, primary_ref, primary_source
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as svc


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated, fx_rates):
    """Point every store the add_transaction path touches at tmp files; base=SGD.

    accounts/transactions are DB-backed now (plan 3 task 4); categories are DB-backed too
    (plan 4a task 2) — db_isolated redirects the process-wide DB singleton, so
    svc.transactions/svc.accounts resolve against a fresh, empty database with no
    per-instance path-sync needed. Reseed BUILTIN_CATEGORIES here (tests/conftest.py's
    shared autouse seed ran BEFORE db_isolated activated, so it landed in the old,
    now-orphaned default engine)."""
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    acct = svc.accounts.create(
        {
            "name": "Meridian Bank",
            "kind": "asset",
            "type": "credit",
            "currency": "USD",
            "initial_balance": 0,
        }
    )
    return {"tmp": tmp_path, "account_id": acct["id"]}


def _add(account_id: str, *, external_id: str | None = None, amount: float = 11.24):
    inp = {
        "description": "Meridian Bank Anthropic USD charge",
        "amount": amount,
        "category": "shopping",
        "account_id": account_id,
        "currency": "USD",
    }
    if external_id is not None:
        source, ref = _pair(external_id)
        inp["external_refs"] = [{"source": source, "ref": ref}]
    text, _ = asyncio.run(execute_tool("add_transactions", inp))
    return json.loads(text)


def _pair(external_id: str) -> tuple[str | None, str]:
    """Split a legacy combined key into the stored (external_source, external_ref) pair."""
    source, _, ref = external_id.partition(":")
    return (source or None, ref)


# ── 1. Two writes, same external_ref → exactly ONE row (the no-dupe property) ────────────


def test_same_external_id_logged_twice_is_one_row(isolated):
    """Call the REAL ledger write twice with the SAME email's external_id → exactly ONE
    transaction row. This is the load-bearing guarantee: re-presenting an already-logged
    email can never create a duplicate, regardless of LLM behavior."""
    ext = "email:<m-102@x>"

    r1 = _add(isolated["account_id"], external_id=ext)
    assert r1["ok"] is True
    first_id = r1["transaction"]["id"]
    assert primary_pair(r1["transaction"]) == _pair(ext)

    # Second call with the SAME external_id — simulates the re-scan window re-presenting the
    # email and the agent (or a re-issuing LLM) logging it again.
    r2 = _add(isolated["account_id"], external_id=ext)
    assert r2["ok"] is True
    # No-op: returns the EXISTING row (same id), not a new one — and SAYS so. Reporting it
    # under the create shape let a scheduled run count two no-ops as newly logged rows
    # (2026-08-12); the guarantee below held throughout, only the answer was wrong.
    assert r2["created"] == 0
    assert r2["already_recorded"][0]["existing_id"] == first_id

    # The ledger holds EXACTLY ONE row carrying this external_id.
    rows = [t for t in svc.transactions.all() if (primary_source(t), primary_ref(t)) == _pair(ext)]
    assert len(rows) == 1, f"expected ONE row for {ext}, found {len(rows)}"
    # And exactly one transaction total in this isolated store.
    assert len(svc.transactions.all()) == 1


def test_create_transaction_external_id_noop_returns_existing(isolated):
    """Unit-level: create_transaction with an existing external_id returns the stored row
    and writes nothing further."""
    ext = "email:<charge-a@bank>"
    data = {
        "date": "2026-06-07",
        "kind": "expense",
        "description": "Charge A",
        "category": "shopping",
        "from_account_id": isolated["account_id"],
        "from_amount": 50.0,
        "from_currency": "USD",
        "external_refs": [{"source": _pair(ext)[0], "ref": _pair(ext)[1]}],
    }
    stored = svc.create_transaction(dict(data))
    again = svc.create_transaction(dict(data))
    assert again["id"] == stored["id"]
    assert len(svc.transactions.all()) == 1


# ── 2. Re-scan represents an email, second add_transaction is a no-op → ONE row ──────────


def test_rescan_represented_email_second_log_is_noop(isolated):
    """End-to-end of the re-scan scenario at the ledger layer: run 1 logs the Meridian Bank charge,
    a second run re-presents the SAME email (same message_id → same external_id), the agent
    logs it again → no-op → ONE row. (The fetch re-scan window has its own UID-level tests in
    test_email_rescan_window.py; here we prove the ledger half of the safety net.)"""
    ext = "email:<m-102@x>"  # same message_id presented on run 1 AND run 2

    # Run 1: the agent reads the email and logs it.
    _add(isolated["account_id"], external_id=ext)
    # Run 2: the re-scan window re-presents the same email; the agent logs it again.
    _add(isolated["account_id"], external_id=ext)
    # Belt-and-suspenders: even a buggy/re-issuing LLM hitting it a third time stays a no-op.
    _add(isolated["account_id"], external_id=ext)

    assert len(svc.transactions.all()) == 1


# ── 3. Behavior unchanged when external_ref is absent ───────────────────────────────────


def test_no_external_id_each_call_needs_the_caller_to_confirm_the_second(isolated):
    """Ordinary manual entries (no external_id) are not ref-deduped — but since 2026-08-10 an
    identical second entry is not booked silently either. It aborts with the pair and books on
    an explicit ``create`` decision, so two real same-day charges are still possible; what is
    gone is booking the accidental double without anyone deciding.
    """
    assert _add(isolated["account_id"])["ok"] is True

    second = _add(isolated["account_id"])
    assert second["ok"] is False
    assert second["already_recorded"][0]["existing_id"]
    assert len(svc.transactions.all()) == 1, "nothing written while undecided"

    text, _ = asyncio.run(
        execute_tool(
            "add_transactions",
            {
                "transactions": [
                    {
                        "description": "Meridian Bank Anthropic USD charge",
                        "amount": 11.24,
                        "category": "shopping",
                        "account_id": isolated["account_id"],
                        "currency": "USD",
                    }
                ],
                "decisions": [{"index": 0, "action": "create"}],
            },
        )
    )
    assert json.loads(text)["ok"] is True
    assert len(svc.transactions.all()) == 2


def test_distinct_external_ids_create_distinct_rows(isolated):
    """Two different emails (distinct message_ids) each create their own row."""
    _add(isolated["account_id"], external_id="email:<a@x>")
    _add(isolated["account_id"], external_id="email:<b@x>")
    assert len(svc.transactions.all()) == 2


def test_distinct_ios_shortcut_refs_create_distinct_rows(isolated):
    """Two Apple Pay taps captured by the iOS shortcut — same merchant, amount and day, e.g.
    two vending-machine buys — each carry their own client-side ``tap:<timestamp>`` ref
    (docs/shortcuts/ios-tap-to-log.md). Distinct refs under the authoritative ``ios-shortcut``
    namespace must create two rows, exactly like two distinct email message-ids, not collapse
    into a content-only near-duplicate refusal."""
    _add(isolated["account_id"], external_id="ios-shortcut:tap:2026-09-03T11:44:12")
    second = _add(isolated["account_id"], external_id="ios-shortcut:tap:2026-09-03T11:44:36")
    assert second["ok"] is True
    assert len(svc.transactions.all()) == 2


def test_blank_external_id_does_not_dedupe(isolated):
    """An empty-string external_id is treated as absent — it is never itself a dedup KEY.

    The second add is still stopped, but on CONTENT (same account, amount and description on
    one day), not on a ref match: since 2026-08-10 any matched row aborts and hands back the
    pair instead of booking-and-flagging. What this pins is that the blank ref contributed
    nothing to that — say the row is distinct and both entries stand, which an exact-ref
    collapse could never allow.
    """
    first = _add(isolated["account_id"], external_id="")
    assert first["ok"] is True

    second = _add(isolated["account_id"], external_id="")
    assert second["ok"] is False, "content dedup stops it"
    match = second["already_recorded"][0]
    assert "exact" not in match["reason"], "the blank ref must not read as an exact-id match"

    # The caller says they are two real charges — both stand.
    text, _ = asyncio.run(
        execute_tool(
            "add_transactions",
            {
                "transactions": [
                    {
                        "description": "Meridian Bank Anthropic USD charge",
                        "amount": 11.24,
                        "category": "shopping",
                        "account_id": isolated["account_id"],
                        "currency": "USD",
                        "external_refs": [],
                    }
                ],
                "decisions": [{"index": 0, "action": "create"}],
            },
        )
    )
    assert json.loads(text)["ok"] is True
    assert len(svc.transactions.all()) == 2


# ── 4. Soft-delete unblocks re-logging (Finding 1 regression) ────────────────────────────


def test_soft_delete_unblocks_relog(isolated):
    """Regression for Finding 1: if an operator deletes a mis-logged email transaction,
    the same email MUST be re-loggable (the deleted row must NOT block a new insert).

    Before the fix, by_external_ref / has_external_ref matched soft-deleted rows, so
    create_transaction silently returned the dead row as a no-op — the email could never
    be re-logged. After the fix, deleted rows are excluded from the dedup check.
    """
    ext = "email:<m-del@bank>"

    # Step 1: log the email normally.
    r1 = _add(isolated["account_id"], external_id=ext)
    assert r1["ok"] is True
    first_id = r1["transaction"]["id"]

    # Step 2: operator realises the transaction is wrong and deletes it.
    svc.transactions.soft_delete(first_id)
    deleted_row = svc.transactions.find_by_id(first_id)
    assert deleted_row["deleted"] is True

    # Step 3: on the next re-scan the agent sees the same email and logs it again.
    r2 = _add(isolated["account_id"], external_id=ext)
    assert r2["ok"] is True
    # Must produce a NEW row, not return the deleted one.
    assert r2["transaction"]["id"] != first_id
    assert r2["transaction"]["deleted"] is not True

    # The store now has exactly 2 rows for this external_ref: one deleted, one live.
    all_rows = [
        t for t in svc.transactions.all() if (primary_source(t), primary_ref(t)) == _pair(ext)
    ]
    assert len(all_rows) == 2
    live_rows = [t for t in all_rows if not t.get("deleted")]
    assert len(live_rows) == 1
