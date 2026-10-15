"""Regressions for the 2026-07-20 confabulation incident.

Two defects, one root cause: the model was handed opaque ids with no meaning, and stale
slug-keyed profile data that contradicted them. In a real transcript (messages 5-10) it
answered from ids alone —
INVENTED a category ("Food & Dining") that does not exist in the book for two rows, and
reported three more as having NO category when every one carried an id. Neither error was
hedged. See ``docs/adr/2026-07-20-resolve-ids-and-drop-dead-profile-fields.md``.

Offline — monkeypatched stores, no LLM / network.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from backend.ai.tools import execute_tool
from backend.services import categories as cat
from backend.services import labels
from backend.services import ledger as svc

TODAY = date.today().isoformat()


def call(tool, **inp):
    text, _proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text)


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr("backend.core.fx.base_currency", lambda: "SGD")
    return tmp_path


@pytest.fixture
def book(env):
    """One account + the incident's real category ("Daily Meals", NOT "Food & Dining") + a
    transaction booked against it."""
    acc = svc.accounts.create(
        {
            "name": "Meridian Bank Multiplier (SGD)",
            "kind": "asset",
            "type": "cash",
            "platform": "",
            "currency": "SGD",
            "initial_balance": 0,
            "archived": False,
        }
    )
    meals = cat.create_category(label="Daily Meals", kind="expense")
    txn = svc.create_transaction(
        {
            "date": TODAY,
            "kind": "expense",
            "description": "Grab Food",
            "category": meals["id"],
            "from_account_id": acc["id"],
            "from_amount": 14.54,
            "from_currency": "SGD",
        }
    )
    return {"account": acc, "category": meals, "txn": txn}


# ── Defect 1: an opaque id arrives WITH its label ──────────────────────────────


def test_get_transactions_ships_the_category_LABEL_beside_the_opaque_id(book):
    """The regression. `category: "cat_13095ba70000"` with no label is exactly what
    let the model invent a name; the label must ride in the same row."""
    rows = call("get_transactions", limit=25)["transactions"]
    row = next(r for r in rows if r["id"] == book["txn"]["id"])

    assert row["category"] == book["category"]["id"], "the opaque id itself must still be there"
    assert row.get("category_label") == "Daily Meals", (
        f"row carries category={row.get('category')!r} with category_label="
        f"{row.get('category_label')!r} — a bare id is the shape that produced a fabricated "
        "category name on real account data"
    )


def test_get_transactions_ships_the_account_NAME_beside_the_opaque_id(book):
    rows = call("get_transactions", limit=25)["transactions"]
    row = next(r for r in rows if r["id"] == book["txn"]["id"])
    assert row["from_account_id"] == book["account"]["id"]
    assert row.get("from_account_name") == "Meridian Bank Multiplier (SGD)"


def test_label_maps_reads_the_REAL_store_not_a_reconstruction(book):
    maps = labels.label_maps(categories=True, accounts=True)
    assert maps["categories"][book["category"]["id"]] == "Daily Meals"
    assert maps["accounts"][book["account"]["id"]] == "Meridian Bank Multiplier (SGD)"


def test_split_lines_are_labelled_without_mutating_the_stored_row():
    """A split txn stores category="split" up top, so the per-line categories are the ONLY
    category information in the payload. They must be labelled — and labelling must COPY,
    never write back into the store's own cached row (``dict(t)`` is shallow)."""
    stored_split = {"category": "cat_food", "amount": 5.0}
    stored = {"id": "t1", "category": "split", "splits": [stored_split]}

    out = labels.label_txn(stored, {"categories": {"cat_food": "Daily Meals"}})

    assert out["splits"][0]["category_label"] == "Daily Meals"
    assert "category_label" not in stored_split, "labelling leaked back into the stored row"
    assert stored["splits"] is not out["splits"]


def test_an_unresolvable_id_attaches_NOTHING_rather_than_an_empty_label():
    """Absent reads as "no label available"; "" reads as "this thing is named nothing". The
    second is a claim, and a wrong claim about a name is the whole bug."""
    out = labels.label_txn({"category": "cat_ghost"}, {"categories": {"cat_food": "Daily Meals"}})
    assert "category_label" not in out
    assert out["category"] == "cat_ghost", "the id itself is never removed or altered"


def test_labelling_is_purely_ADDITIVE_and_removes_no_money_field():
    """The 2026-07-09 regression: ``_project_txn`` is ``return dict(t)`` because a curated allowlist
    once silently hid fields. Labels must only ever ADD keys — never select any away."""
    stored = {
        "id": "t1",
        "category": "cat_food",
        "amount": 12.40,
        "base_amount": 16.01,
        "base_currency": "SGD",
        "fx_rate": 1.291429,
    }
    out = labels.label_txn(stored, {"categories": {"cat_food": "Daily Meals"}})

    assert set(stored).issubset(set(out)), "a stored field went missing"
    for key, value in stored.items():
        assert out[key] == value, f"{key} was altered"
    assert set(out) - set(stored) == {"category_label"}


def test_lot_and_income_rows_get_instrument_and_account_names():
    """The same defect class on ``get_lots`` / ``get_income`` — a money-bearing row whose only
    identification of WHAT was bought is an opaque uuid."""
    maps = {
        "instruments": {"i1": "ONWAI Corp"},
        "instrument_tickers": {"i1": "ONWAI"},
        "accounts": {"acc_1": "Vantage Brokerage"},
    }
    out = labels.label_instrument_row(
        {"instrument_id": "i1", "account_id": "acc_1", "remaining_qty": 1242.7}, maps
    )
    assert out["instrument_label"] == "ONWAI Corp"
    assert out["instrument_ticker"] == "ONWAI"
    assert out["account_name"] == "Vantage Brokerage"
    assert out["remaining_qty"] == 1242.7


# ── Defect 2: get_profile no longer ships the dead fields ─────────────────────

_DEAD = (
    "custom_categories",
    "deleted_builtins",
    "hidden_categories",
    "category_color_overrides",
)


def test_get_profile_no_longer_ships_the_dead_category_fields(tmp_path, monkeypatch):
    """They were 1,696 of 3,599 response bytes (47%) and keyed by RETIRED SLUGS while every
    live category id is an opaque cat_<12hex> — two contradictory shapes for one concept,
    both presented as current, on a CORE tool every session pays for."""
    from backend.db.profile_store import ProfileStore

    stored = {
        "base_currency": "SGD",
        "custom_categories": [{"key": "startup", "label": "Startup Costs"}],
        "deleted_builtins": ["stuff_i_forgot"],
        "hidden_categories": [],
        "category_color_overrides": {"family": "#e2554f"},
    }
    # profile is DB-backed now (Wave 5, JSON->DB) — seed directly through ProfileStore.
    ProfileStore().set_raw(stored)

    profile = call("get_profile")

    present = [f for f in _DEAD if f in profile]
    assert not present, f"get_profile still ships dead category fields: {present}"
    assert profile["base_currency"] == "SGD", "a live field was collaterally stripped"


def test_read_profile_strips_the_dead_fields_even_when_still_stored(tmp_path, monkeypatch):
    """The stored copies survive until the one-shot migration runs, so the READ path has to
    strip them — otherwise an un-migrated install keeps serving the contradiction."""
    from backend.db.profile_store import ProfileStore
    from backend.services import profile as profile_service

    stored = {
        "base_currency": "SGD",
        "custom_categories": [{"key": "startup"}],
        "deleted_builtins": ["stuff_i_forgot"],
        "hidden_categories": [],
        "category_color_overrides": {"family": "#e2554f"},
        "category_notes": {"family_support": "Help with a relative's expenses"},
    }
    # profile is DB-backed now (Wave 5, JSON->DB) — seed directly through ProfileStore.
    ProfileStore().set_raw(stored)

    out = profile_service.read_profile()

    for field in _DEAD:
        assert field not in out, f"{field} survived read_profile"
