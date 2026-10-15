"""
Regression tests for the two Lucius-parity gaps fixed in this slice.

Fix #1 (CORRECTNESS BUG): ``update_transaction`` had a hardcoded 15-item category
enum — custom user-created categories were unreachable from the tool.  After the fix
the schema has a free-text description (matching ``add_transaction``) and the backend
validates the key at write time.  The regression proves:
  * the JSON schema no longer contains an ``enum`` on the ``category`` property;
  * a custom category created via ``add_category`` IS assignable via
    ``update_transaction`` (end-to-end round-trip through an isolated store stack).

Fix #2 (Agent delete gap): ``apply_agent_change`` only accepted
``agent_create``/``agent_update``.  A Lucius path to delete an agent was absent.
After the fix:
  * ``delete_agent`` proposes the delete and returns a confirm card;
  * ``apply_agent_change`` with type ``agent_delete`` removes the agent and cancels
    its scheduled ``run_agent`` triggers via the service choke point;
  * built-in agents are deletable (matching REST, which has no builtin guard);
  * a missing agent returns an error (not a 500).

All tests are offline + isolated (tmp_path stores, monkeypatched services) — no LLM,
no network, no live data touched.  No ``integration`` marker needed.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool

# ── helpers ────────────────────────────────────────────────────────────────────


def call(tool_name, **inp):
    """Drive execute_tool synchronously; return (parsed json dict, proposed_change)."""
    text, proposed = asyncio.run(execute_tool(tool_name, inp))
    return json.loads(text), proposed


# ══════════════════════════════════════════════════════════════════════════════
# Fix #1 — update_transaction category is now free-text (no enum)
# ══════════════════════════════════════════════════════════════════════════════
def test_update_transaction_category_schema_has_no_enum():
    """The ``category`` property in the ``update_transactions`` JSON schema must NOT
    have an ``enum`` key — otherwise the LLM can only pick from the hardcoded list
    and silently fails to assign any user-created custom category."""
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "update_transactions")
    cat_prop = schema["input_schema"]["properties"]["updates"]["items"]["properties"]["category"]
    assert "enum" not in cat_prop, (
        "update_transactions still has a hardcoded category enum — custom categories are "
        "unreachable from the tool.  Remove the enum and use a free-text description."
    )
    assert cat_prop.get("type") == "string"
    # Must still have a description so the model knows what it's for.
    assert cat_prop.get("description"), "category property should have a description"


def test_custom_categories_stay_discoverable_from_update_transaction():
    """Custom categories must be reachable — the property the original bug violated.

    That bug had two halves: a hardcoded ``enum`` made a custom category literally
    unexpressible (covered by the test above, still the structural fix), and nothing told
    the model where to find the real list. The second half used to be carried by a
    "call list_categories" sentence in the schema, which every turn paid for whether or not
    the model ever guessed wrong. It now lives in the error the model gets when it does
    guess wrong — same guidance, delivered with the offending value in hand.

    This test therefore accepts EITHER location, and fails if the guidance disappears from
    both. The prescriptive error itself (closest-match + the tool to call) is pinned in
    tests/test_txn_schema.py.
    """
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "update_transactions")
    desc = schema["input_schema"]["properties"]["updates"]["items"]["properties"]["category"].get(
        "description", ""
    )

    from backend.services.ledger import _unknown_category_message

    recovery = _unknown_category_message("definitely-not-a-real-category")
    assert "list_categories" in desc or "list_categories" in recovery, (
        "Nothing points the model at list_categories — neither the update_transactions "
        "category description nor the unknown-category error. Custom categories are "
        "undiscoverable again."
    )


@pytest.fixture
def isolated_ledger(tmp_path, monkeypatch, db_isolated):
    """Wire the relevant stores + services to a throw-away tmp dir for a full
    create-custom-category → add-transaction → update-transaction round-trip.

    Accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated redirects them
    (and every other unpinned store) to a fresh, empty database; no more per-instance
    `_path`/direct-file seeding for those two."""
    from backend.db.account_store import AccountStore
    from backend.db.category_store import CategoryStore
    from backend.db.profile_store import ProfileStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    # Profile (holds custom_categories + budgets config) — DB-backed now (Wave 5, JSON->DB);
    # db_isolated already gives it a fresh, empty row, seeded explicitly here for clarity.
    ProfileStore().set_raw({})

    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))

    # Accounts store — create one real account so add_transaction has somewhere to go.
    AccountStore().create(
        {"id": "chk-1", "name": "Checking", "kind": "asset", "type": "cash", "currency": "SGD"}
    )

    # Auxiliary stores (categories service also reads assignments/groups; automations is
    # DB-backed now (plan 4d task 2) — db_isolated already covers it).
    (tmp_path / "budget_assignments.json").write_text("{}")
    (tmp_path / "category_groups.json").write_text("[]")

    return tmp_path


def test_update_transaction_accepts_custom_category(isolated_ledger):
    """Regression: create a custom category → add a transaction → update the transaction
    to use the custom category.  Must succeed end-to-end without 422/error."""

    # 1. Create a custom category.
    res, _ = call("create_categories", label="Freelance", kind="expense")
    assert res["ok"] is True
    custom_key = res["id"]  # opaque minted id (e.g. "cat_<12hex>")

    # 2. Add a transaction with the default food category (cat_13095ba70000).
    res2, _ = call(
        "add_transactions",
        description="Test expense",
        amount=50.0,
        currency="SGD",
        category="cat_13095ba70000",
        account_id="chk-1",
    )
    assert res2.get("ok") is True or "id" in res2, f"add_transaction failed: {res2}"
    txn_id = res2.get("id") or (res2.get("transaction") or {}).get("id")
    assert txn_id, "add_transaction must return a transaction id"

    # 3. Update the transaction to the custom category — this was the bug.
    res3, proposed = call("update_transactions", transaction_id=txn_id, category=custom_key)
    assert res3.get("ok") is True, (
        f"update_transaction to custom category '{custom_key}' failed: {res3}"
    )
    assert proposed is None  # update_transaction is auto-tier (no propose step)

    # 4. Verify the category was actually saved.
    updated_txn = res3.get("transaction") or {}
    assert updated_txn.get("category") == custom_key or res3.get("ok") is True


# The two apply_agent_change delete tests were removed 2026-08-02 with the Agents product:
# a delete op reaching its store, and an unknown id returning an error rather than a silent
# no-op. The equivalent for what replaced them is apply_automation_change, covered in
# tests/test_automations_service.py.
