"""The labelled budget reads Lucius works from (``services.budget_catalog``, owner 2026-09-26):
``list_envelopes`` rows are the whole envelope — group and linked categories by NAME, the real
Zero-Based target and note (never the stale copies older rows still carry), 50/30/20's
cap once set up — with combinable filters; ``list_categories`` says which envelope each
category is in; ``get_budget_month`` rows carry the same labels and the note."""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.db.category_store import CategoryStore
from backend.services import envelopes as envelopes_service
from backend.services import planning

CATS = [
    {"id": "cat_rent01", "label": "Rent", "kind": "expense", "color": "#e2554f"},
    {"id": "cat_food01", "label": "Groceries", "kind": "expense", "color": "#1d9e75"},
    {"id": "cat_fun001", "label": "Cinema", "kind": "expense", "color": "#22b8cf"},
    {"id": "cat_pay001", "label": "Salary", "kind": "income", "color": "#34b08a"},
]


def _call(tool: str, **inp) -> dict:
    text, proposed = asyncio.run(execute_tool(tool, inp))
    assert proposed is None
    return json.loads(text)


@pytest.fixture
def book(db_isolated):
    """Bills: Rent (target + note) · Living: Groceries (no target) · Cinema: unlinked."""
    CategoryStore().bulk_insert(CATS)
    bills = planning.apply_budget_change({"type": "group_create", "name": "Bills"})
    living = planning.apply_budget_change({"type": "group_create", "name": "Living"})
    gid = lambda r: (r.get("group") or r)["id"]  # noqa: E731
    rent = envelopes_service.create_envelope("Rent", group_id=gid(bills))["id"]
    food = envelopes_service.create_envelope("Groceries", group_id=gid(living))["id"]
    envelopes_service.add_category(rent, "cat_rent01")
    envelopes_service.add_category(food, "cat_food01")
    planning.write_category_targets({rent: {"type": "monthly", "amount": 2150}})
    planning.write_category_note(rent, "Due on the 1st")
    return {"rent": rent, "food": food}


def test_list_envelopes_rows_are_labelled_and_read_the_real_target_and_note(book):
    rows = {r["name"]: r for r in _call("list_envelopes")["envelopes"]}
    rent = rows["Rent"]
    assert rent["group"]["name"] == "Bills"
    assert rent["linked_categories"] == [{"id": "cat_rent01", "label": "Rent"}]
    assert rent["zero_based_target"]["amount"] == 2150
    assert rent["note"] == "Due on the 1st"
    assert rent["managed_by_app"] is False
    assert rows["Groceries"]["zero_based_target"] is None
    # The raw store fields are gone: ids arrive with names, and no stale target/note copy.
    for raw in ("group_id", "category_ids", "target", "order"):
        assert raw not in rent
    # 50/30/20 was never set up — no shares to report.
    assert "fifty_thirty_twenty" not in rent


def test_list_envelopes_filters_combine_and_an_unknown_name_says_what_exists(book):
    names = lambda **f: [r["name"] for r in _call("list_envelopes", **f)["envelopes"]]  # noqa: E731
    assert names(group="bills") == ["Rent"]
    assert names(category="Groceries") == ["Groceries"]
    assert names(search="GROC") == ["Groceries"]
    assert names(has_target=True) == ["Rent"]
    assert names(envelope_ids=[book["food"]]) == ["Groceries"]
    assert names(group="Living", has_target=True) == []
    err = _call("list_envelopes", group="Bils")["error"]
    assert "Bills" in err and "list_envelopes" in err


def test_list_categories_says_which_envelope_each_is_in(book):
    cats = {c["label"]: c for c in _call("list_categories")["categories"]}
    assert cats["Rent"]["envelope"] == {"id": book["rent"], "name": "Rent"}
    assert cats["Cinema"]["envelope"] is None  # its spending is in no envelope
    labels = lambda **f: sorted(c["label"] for c in _call("list_categories", **f)["categories"])  # noqa: E731
    assert labels(linked=False, kind="expense") == ["Cinema"]
    assert labels(envelope="Rent") == ["Rent"]
    assert labels(kind="income") == ["Salary"]


def test_get_budget_month_rows_carry_linked_categories_and_the_note(book):
    table = _call("get_budget_month", month=date.today().strftime("%Y-%m"))
    rows = {r["label"]: r for g in table["groups"] for r in g.get("categories") or []}
    assert rows["Rent"]["linked_categories"] == [{"id": "cat_rent01", "label": "Rent"}]
    assert rows["Rent"]["note"] == "Due on the 1st"
    assert rows["Rent"]["category_ids"] == ["cat_rent01"]  # additive: nothing removed


def test_fifty_thirty_twenty_cap_rides_on_the_row_once_set_up(book, monkeypatch):
    """Once 50/30/20 is configured, list_envelopes rows carry `fifty_thirty_twenty` — now
    just `{"cap": ...}` (owner, 2026-09-26): the method's groups are the book's own
    Zero-Based groups now, so there is no more per-envelope bucket to report here."""
    from backend.services.budget_engines import service as engines

    monkeypatch.setattr(engines, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(engines, "base_currency", lambda: "SGD")
    engines.apply_verb("proportional", "configure", {})
    rows = {r["name"]: r for r in _call("list_envelopes")["envelopes"]}
    assert rows["Groceries"]["fifty_thirty_twenty"] == {"cap": None}

    month = date.today().strftime("%Y-%m")
    engines.apply_verb(
        "proportional", "set_cap", {"envelope_id": book["food"], "month": month, "amount": 300}
    )
    rows = {r["name"]: r for r in _call("list_envelopes")["envelopes"]}
    assert rows["Groceries"]["fifty_thirty_twenty"] == {"cap": 300.0}


def test_no_tool_sends_lucius_to_the_retired_apply_budget_change():
    """The confirm card applies itself (ADR 2026-08-25) — ``apply_budget_change`` is the service
    choke, not a tool. Six descriptions still said "after the user confirms, apply via
    apply_budget_change", pointing the model at a tool that doesn't exist."""
    offenders = [
        t["name"]
        for t in TOOL_SCHEMAS
        if "apply via apply_budget_change" in t["description"]
        or "→ apply_budget_change" in t["description"]
    ]
    assert not offenders, offenders
