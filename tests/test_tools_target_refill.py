"""
Tests for needs_whole_amount threading in set_envelope_targets + apply_budget_change.

Verifies:
- needs_whole_amount=False → stored TargetBody carries needs_whole_amount=False (refill mode)
- needs_whole_amount=True → stored TargetBody omits the flag (truthy default, lean storage)
- Omitting needs_whole_amount → default True behaviour, flag absent from stored spec
- set_envelope_targets (propose) echoes needs_whole_amount in the proposed_change + display
- apply_budget_change schema lists needs_whole_amount

Fully isolated: budgets_service.PROFILE_JSON and the planning assignments store are
monkeypatched to tmp_path; no live data is touched. No LLM, no network.

Run:
    .venv/bin/python3 -m pytest tests/test_tools_target_refill.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool


def apply_now(**change):
    """Arrange state through the write choke directly.

    Setup is not the thing under test: these used to call ``apply_budget_change``, which is
    deleted (the propose tool writes on approval now — ADR
    2026-08-25-one-confirm-mechanism-for-every-tool). Routing setup through a confirm card
    would make every arrangement a two-step dance for no coverage."""
    from backend.services import planning as _svc

    return _svc.apply_budget_change(change), None


@pytest.fixture(autouse=True)
def _card_capable(monkeypatch):
    """A card-capable surface — these tests drive the propose→confirm flow. Still yields to
    a REDEMPTION, so a ✓ writes rather than drawing another card."""
    from backend.ai.tools import registry

    monkeypatch.setattr(
        registry, "should_preview_write", lambda: not registry.is_redeeming_approval()
    )


# A gated tool binds its card to the tool_use_id of the call being gated.
_TOOL_USE_ID = "toolu_refill_test"


def confirm(proposed: dict):
    """Approve a staged card the way the SERVER does — re-run the staging tool on the args
    it stored."""
    return call(proposed["grant_tool"], confirm_token=proposed["tool_use_id"])


def call(tool_name: str, **inp):
    text, proposed = asyncio.run(execute_tool(tool_name, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


@pytest.fixture
def isolated():
    """Seed the profile (DB-backed, ProfileStore — Wave 5) + assignments store."""
    from backend.db.profile_store import ProfileStore
    from backend.services import envelopes as envelopes_service

    ProfileStore().set_raw({"base_currency": "SGD"})
    # The envelope service holds its OWN store instances. Full-split (RFC
    # envelope-category-full-split.md §Backend 3/4) deleted the pseudo-row fallback AND
    # re-keyed target storage to envelope id, so "cat_13095ba70000"/"cat_db5642ab0000" need REAL 1:1 envelopes.
    # budget_assignments is DB-backed now (plan 4a task 4) too — like envelopes/targets/profile,
    # it rides the shared default engine here (this fixture never isolates the DB), auto-cleared
    # between tests by tests/conftest.py's autouse fixture; this file's own assertions never
    # touch assigned amounts.
    envelopes_service.ensure_envelopes_migrated()

    def _read_targets():
        raw = ProfileStore().get_raw()
        return raw.get("category_targets") or {}

    def _target_key(category):
        """Targets are ENVELOPE-keyed storage now — resolve a category to its slot."""
        return envelopes_service.env_of()[category]

    return {"read_targets": _read_targets, "target_key": _target_key}


# ── schema presence ───────────────────────────────────────────────────────────────


def test_set_category_target_schema_has_needs_whole_amount():
    # The field lives in the BATCH ITEM since the 2026-07-19d dual-shape cleanup: the root
    # no longer advertises a duplicate copy of each item's fields (schema_of_batch_only).
    # The point of this test — the model can see and set needs_whole_amount — is unchanged;
    # only where it is described moved.
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "set_envelope_targets")
    props = schema["input_schema"]["properties"]["targets"]["items"]["properties"]
    assert "needs_whole_amount" in props
    assert props["needs_whole_amount"]["type"] == "boolean"


def test_the_target_tool_schema_has_needs_whole_amount():
    """``apply_budget_change`` is deleted — the flag lives on the tool that STAGES the change
    (ADR 2026-08-25-one-confirm-mechanism-for-every-tool)."""
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "set_envelope_targets")
    # Batch-only schema: the per-target fields live on the array's item shape.
    item = schema["input_schema"]["properties"]["targets"]["items"]["properties"]
    assert "needs_whole_amount" in item
    assert item["needs_whole_amount"]["type"] == "boolean"


# ── set_envelope_targets propose (APPROVE-TIER) ──────────────────────────────────


def test_propose_refill_echoes_flag_in_proposed_change(isolated):
    result, proposed = call(
        "set_envelope_targets",
        envelope_id=isolated["target_key"]("cat_13095ba70000"),
        type="monthly",
        amount=500.0,
        needs_whole_amount=False,
    )
    assert result is None
    assert proposed is not None
    assert proposed["needs_whole_amount"] is False
    assert proposed["display"]["needs_whole_amount"] is False


def test_propose_set_aside_echoes_flag_in_proposed_change(isolated):
    result, proposed = call(
        "set_envelope_targets",
        envelope_id=isolated["target_key"]("cat_13095ba70000"),
        type="monthly",
        amount=500.0,
        needs_whole_amount=True,
    )
    assert result is None
    assert proposed["needs_whole_amount"] is True
    assert proposed["display"]["needs_whole_amount"] is True


def test_propose_omitted_needs_whole_amount_not_in_change(isolated):
    """When the caller omits needs_whole_amount the key must not appear in the change dict,
    so the service default (True) is picked up without overriding it."""
    result, proposed = call(
        "set_envelope_targets",
        envelope_id=isolated["target_key"]("cat_13095ba70000"),
        type="monthly",
        amount=500.0,
    )
    assert result is None
    assert "needs_whole_amount" not in proposed
    assert proposed["display"]["needs_whole_amount"] is None


# ── apply_budget_change (write path) ─────────────────────────────────────────────


def test_apply_set_target_refill_persists_flag(isolated):
    """needs_whole_amount=False must be written into the stored TargetBody."""
    result, proposed = apply_now(
        type="set_target",
        envelope_id=isolated["target_key"]("cat_13095ba70000"),
        target_type="monthly",
        amount=600.0,
        needs_whole_amount=False,
    )
    assert result.get("ok") is True
    stored = isolated["read_targets"]()
    key = isolated["target_key"]("cat_13095ba70000")
    assert key in stored
    target = stored[key]
    assert target["type"] == "monthly"
    assert target["amount"] == 600.0
    assert target.get("needs_whole_amount") is False


def test_apply_set_target_set_aside_omits_flag(isolated):
    """needs_whole_amount=True (the default) must NOT be written to keep stored data lean."""
    result, proposed = apply_now(
        type="set_target",
        envelope_id=isolated["target_key"]("cat_13095ba70000"),
        target_type="monthly",
        amount=600.0,
        needs_whole_amount=True,
    )
    assert result.get("ok") is True
    stored = isolated["read_targets"]()
    key = isolated["target_key"]("cat_13095ba70000")
    assert key in stored
    target = stored[key]
    # True is the default — the service omits it from the stored spec for lean storage.
    assert "needs_whole_amount" not in target


def test_apply_set_target_omitted_stores_set_aside_by_default(isolated):
    """Omitting needs_whole_amount must leave the stored spec without the flag."""
    result, proposed = apply_now(
        type="set_target",
        envelope_id=isolated["target_key"]("cat_db5642ab0000"),
        target_type="yearly",
        amount=1200.0,
    )
    assert result.get("ok") is True
    stored = isolated["read_targets"]()
    key = isolated["target_key"]("cat_db5642ab0000")
    assert key in stored
    assert "needs_whole_amount" not in stored[key]


def test_roundtrip_propose_then_apply_refill(isolated):
    """Full propose → apply roundtrip: the proposed_change dict from set_envelope_targets
    feeds directly into apply_budget_change and produces a refill target."""
    _, proposed = call(
        "set_envelope_targets",
        envelope_id=isolated["target_key"]("cat_13095ba70000"),
        type="monthly",
        amount=750.0,
        needs_whole_amount=False,
    )
    # Strip carrier fields exactly as the confirm card does.
    apply_input = {
        k: v for k, v in proposed.items() if k not in ("idempotency_key", "kind", "display")
    }
    result, _ = apply_now(**apply_input)
    assert result.get("ok") is True
    stored = isolated["read_targets"]()
    key = isolated["target_key"]("cat_13095ba70000")
    assert stored[key].get("needs_whole_amount") is False
