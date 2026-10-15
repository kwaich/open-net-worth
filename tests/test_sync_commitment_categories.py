"""
Unit tests for planning.sync_commitment_envelopes (category-opaque-ids migration, decision
#4 — supersedes the old commitment_{id} bot-managed CATEGORY convention).

A synced commitment no longer mints a category at all: it gets (or keeps) a dedicated
ZERO-CATEGORY envelope in the pinned 'Commitments' group, linked via the real foreign key
``commitment.envelope_id``. This suite proves that FK stays true across create / idempotent
re-sync / target changes / group drift / retirement (paid off, cancelled, no unpaid
installments).

Fully isolated + offline: every store the code path touches (categories, category groups,
budget assignments, envelopes, transactions, commitments) plus the profile path are
monkeypatched into a pytest tmp_path — mirrors the ``env`` fixture pattern in
tests/test_envelope_flip.py ("the envelope service holds its OWN store instances — always
patch them").
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from backend.db.commitments_store import CommitmentsStore
from backend.db.profile_store import ProfileStore
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning as planning

_FX = {"SGD": 1.0}


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated redirects
    # svc.transactions/.accounts AND categories_service.transaction_store (its own
    # instance) to the SAME fresh, empty per-test database; no per-instance path needed.
    groups_path = tmp_path / "category_groups.json"
    envelopes_path = tmp_path / "envelopes.json"
    profile_path = tmp_path / "profile.json"
    categories_path = tmp_path / "categories.json"

    monkeypatch.setattr(planning, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    # budget_assignments is DB-backed now (plan 4a task 4) — db_isolated covers it, same as
    # CommitmentsStore (plan 4a task 3). categories/category_groups are DB-backed too (plan 4a
    # task 2) — db_isolated already gives each a fresh, empty table, matching the old
    # groups_path/categories_path "[]" seed with no write needed.

    ProfileStore().set_raw({"base_currency": "SGD"})
    groups_path.write_text("[]", encoding="utf-8")
    categories_path.write_text("[]", encoding="utf-8")

    def _w(path, obj):
        # envelopes is DB-backed now (plan 4a task 2) — route the one path a test body
        # actually still writes through (_seed_migrated_book) to the real store.
        if path is envelopes_path:
            envelopes_service.envelope_store.replace_all(obj)
        else:
            path.write_text(json.dumps(obj), encoding="utf-8")

    return type(
        "Env",
        (),
        {
            "w": staticmethod(_w),
            "envelopes_path": envelopes_path,
            "categories_path": categories_path,
            "profile_path": profile_path,
            "groups_path": groups_path,
        },
    )


def _due_in(months: int) -> str:
    d = date.today() + timedelta(days=months * 30)
    return d.isoformat()


def _make_active_commitment(cid="car", amount=1200.0):
    return CommitmentsStore().create(
        {
            "id": cid,
            "name": "Car loan",
            "currency": "SGD",
            "installments": [{"amount": amount, "due_date": _due_in(3)}],
        }
    )


def _seed_migrated_book(env) -> None:
    """Seed a dummy pre-existing envelope (a realistic book always has at least one)."""
    env.w(
        env.envelopes_path,
        [
            {
                "id": "env_seed0000001",
                "name": "Seed",
                "category_ids": ["seed"],
                "group_id": "others",
                "order": 0,
                "archived": False,
            }
        ],
    )


def test_sync_creates_no_category_only_an_envelope(env):
    """The core of decision #4: a synced commitment mints NO category at all — just a
    zero-category envelope, linked via commitment.envelope_id."""
    c = _make_active_commitment()
    result = planning.sync_commitment_envelopes()
    assert result["synced"] == 1

    # No category row — CategoryStore stays empty.
    assert categories_service.get_all_categories() == []

    commitment = CommitmentsStore().find_by_id(c["id"])
    env_id = commitment["envelope_id"]
    assert env_id is not None

    envelope = envelopes_service.envelope_store.find_by_id(env_id)
    assert envelope is not None
    assert envelope["category_ids"] == []  # zero-category
    assert envelope["name"] == "Car loan"
    assert envelope["archived"] is False


def test_sync_mints_envelope_on_a_book_that_already_has_one(env):
    """A pre-existing envelope (any real book has at least one) doesn't block a fresh mint
    for the commitment — envelope creation is always independent."""
    _seed_migrated_book(env)
    _make_active_commitment()
    planning.sync_commitment_envelopes()

    commitment = CommitmentsStore().find_by_id("car")
    envelope = envelopes_service.envelope_store.find_by_id(commitment["envelope_id"])
    assert envelope["category_ids"] == []
    assert envelope["archived"] is False


def test_sync_is_idempotent(env):
    _make_active_commitment()
    first = planning.sync_commitment_envelopes()
    second = planning.sync_commitment_envelopes()
    assert first["synced"] == second["synced"] == 1

    commitment = CommitmentsStore().find_by_id("car")
    non_archived = [e for e in envelopes_service.envelope_store.all() if not e.get("archived")]
    assert len(non_archived) == 1  # not duplicated
    assert non_archived[0]["id"] == commitment["envelope_id"]


def test_sync_retires_envelope_when_commitment_completes(env):
    c = _make_active_commitment()
    planning.sync_commitment_envelopes()
    commitment = CommitmentsStore().find_by_id(c["id"])
    env_id = commitment["envelope_id"]
    assert envelopes_service.envelope_store.find_by_id(env_id)["archived"] is False

    CommitmentsStore().update_commitment(c["id"], {"status": "completed"})
    result = planning.sync_commitment_envelopes()
    assert result["removed"] == 1

    assert envelopes_service.envelope_store.find_by_id(env_id) is None
    assert CommitmentsStore().find_by_id(c["id"])["envelope_id"] is None


def test_sync_retirement_zeroes_assigned_money(env):
    """force_delete_commitment_envelope releases any money parked in the envelope back to
    Ready-to-Assign (mirrors delete_envelope's own zero-and-archive contract)."""
    c = _make_active_commitment()
    planning.sync_commitment_envelopes()
    commitment = CommitmentsStore().find_by_id(c["id"])
    env_id = commitment["envelope_id"]
    month = date.today().strftime("%Y-%m")
    planning.assignments_store.set_assigned(month, env_id, 400.0)

    CommitmentsStore().update_commitment(c["id"], {"status": "completed"})
    planning.sync_commitment_envelopes()

    assert planning.assignments_store.assigned(month, env_id) == 0.0


def _current_month() -> str:
    return date.today().strftime("%Y-%m")


def test_sync_sets_no_target_on_commitment_envelopes(env):
    """The naive "next installment ÷ months-until-due" auto-target was REMOVED (owner 2026-07-10):
    a commitment envelope minted by the sync carries NO target — the funding target is the user's /
    Lucius's to set, never derived. Repeat syncs and installment changes never write one."""
    _seed_migrated_book(env)
    commitment = _make_active_commitment(amount=1200.0)
    planning.sync_commitment_envelopes()
    env_id = CommitmentsStore().find_by_id(commitment["id"])["envelope_id"]
    assert planning._target_for(env_id, planning.read_category_targets()) is None

    # an installment change must NOT conjure a target either
    CommitmentsStore().set_installments(
        commitment["id"], [{"amount": 2400.0, "due_date": _due_in(3)}]
    )
    planning.sync_commitment_envelopes()
    assert planning._target_for(env_id, planning.read_category_targets()) is None
    table = planning.budget_month(_current_month())
    row = next(c for g in table["groups"] for c in g["categories"] if c["envelope_id"] == env_id)
    assert not row.get("target")


def test_sync_rehomes_a_drifted_commitment_envelope(env):
    """EXTRA A: a commitment envelope whose group_id drifted
    away from the pinned Commitments group (e.g. an old Lucius reorg, before move_category
    refused this) self-heals back on the next sync."""
    _seed_migrated_book(env)
    _make_active_commitment()
    result = planning.sync_commitment_envelopes()
    group_id = result["group_id"]
    env_id = CommitmentsStore().find_by_id("car")["envelope_id"]

    # Simulate the drift directly on the store (bypassing the now-guarded write path).
    other_group = planning.groups_store.create({"name": "Somewhere else", "categories": []})
    envelopes_service.envelope_store.update_envelope(env_id, {"group_id": other_group["id"]})
    assert envelopes_service.envelope_store.find_by_id(env_id)["group_id"] == other_group["id"]

    planning.sync_commitment_envelopes()
    assert envelopes_service.envelope_store.find_by_id(env_id)["group_id"] == group_id


def test_is_commitments_synced_reflects_the_envelope_id_fk(env):
    """envelopes.is_commitments_synced now resolves via the commitment.envelope_id FK, not a
    commitment_* category-key prefix (there IS no category anymore)."""
    _make_active_commitment()
    planning.sync_commitment_envelopes()
    env_id = CommitmentsStore().find_by_id("car")["envelope_id"]
    envelope = envelopes_service.envelope_store.find_by_id(env_id)
    assert envelopes_service.is_commitments_synced(envelope) is True

    other = envelopes_service.create_envelope("Groceries")
    assert envelopes_service.is_commitments_synced(other) is False
