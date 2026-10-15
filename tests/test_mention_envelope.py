"""Unit tests for the first-class ``envelope`` mention type.

The envelope type is the budget-row identity of the envelope⊥category full split
(ADR 2026-07-11-envelope-first-class-mention-type): ``search_envelope`` /
``_resolve_envelope`` key rows off ``row.envelope_id`` from the CURRENT month's
``planning.budget_month`` table, so a ZERO-CATEGORY envelope (commitment pot,
card-payment envelope, named savings pot) — which the "category" provider
deliberately skips — is mentionable, suggestible, and drag-able.

Hermetic: monkeypatches ``planning.budget_month`` with a predictable table
(same pattern as the category provider tests).
"""

from __future__ import annotations

from backend.services.mentions.registry import REGISTRY
from backend.services.mentions.types import SearchQuery

_REQUIRED_KEYS = {"type", "id", "label", "sublabel", "value", "icon", "surface"}


def _query(q: str = "", limit: int = 20) -> SearchQuery:
    return SearchQuery(q=q, types=None, surfaces=frozenset({"entity"}), limit=limit, cursor=None)


def _fake_table() -> dict:
    return {
        "month": "2026-07",
        "base_currency": "USD",
        "groups": [
            {
                "name": "Everyday",
                "categories": [
                    {
                        "key": "env_groceries",
                        "envelope_id": "env_groceries",
                        "label": "Groceries",
                        "color": "#22aa66",
                        "category_ids": ["cat_food"],
                        "assigned": 500.0,
                        "activity": -120.0,
                        "available": 380.0,
                        "target": {"type": "monthly", "amount": 600.0, "needs_whole_amount": True},
                        "status": "funded",
                        "is_payment_envelope": False,
                    },
                ],
            },
            {
                "name": "Credit Card Payments",
                "categories": [
                    {
                        "key": "env_cc",
                        "envelope_id": "env_cc",
                        "label": "Amex",
                        "category_ids": [],
                        "assigned": 0.0,
                        "activity": 50.0,
                        "available": 50.0,
                        "target": None,
                        "status": "none",
                        "is_payment_envelope": True,
                        "available_for_payment": 50.0,
                        "underfunded": 25.0,
                    },
                ],
            },
            {
                # Synthetic display group — its rows carry NO envelope_id and must
                # never surface as envelope mentions.
                "name": "Uncategorized",
                "unsorted": True,
                "categories": [
                    {"key": None, "label": "Uncategorized", "activity": -10.0, "available": -10.0},
                ],
            },
        ],
    }


def test_envelope_in_registry():
    assert "envelope" in REGISTRY
    provider = REGISTRY["envelope"]
    assert provider.surfaces == frozenset({"entity"})


def test_search_envelope_covers_all_envelopes_including_zero_category(monkeypatch):
    from backend.services.mentions.providers import search_envelope

    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: _fake_table())
    rows = search_envelope(_query())
    for row in rows:
        assert _REQUIRED_KEYS <= set(row)
        assert row["type"] == "envelope"
        assert row["surface"] == "entity"
        # value must be the envelope id — this is what _resolve_envelope uses.
        assert row["value"] == row["id"]
    ids = [r["id"] for r in rows]
    assert ids == ["env_groceries", "env_cc"], "synthetic Uncategorized row must be skipped"
    by_id = {r["id"]: r for r in rows}
    assert by_id["env_cc"]["label"] == "Amex"
    assert by_id["env_cc"]["sublabel"] == "Credit Card Payments"


def test_search_envelope_substring_match(monkeypatch):
    from backend.services.mentions.providers import search_envelope

    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: _fake_table())
    rows = search_envelope(_query(q="amex"))
    assert [r["id"] for r in rows] == ["env_cc"]
    assert search_envelope(_query(q="zzz-no-match")) == []


def test_search_envelope_type_synonym_lists_all(monkeypatch):
    from backend.services.mentions.providers import search_envelope

    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: _fake_table())
    rows = search_envelope(_query(q="envelopes"))
    assert [r["id"] for r in rows] == ["env_groceries", "env_cc"]
    assert all(r.get("search") for r in rows), "synonym rows carry the search hint"


def test_resolve_envelope_returns_row_slice(monkeypatch):
    from backend.services.mentions.resolve import _resolve_envelope

    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: _fake_table())
    data = _resolve_envelope("env_groceries")
    assert data is not None
    assert data["key"] == "env_groceries"
    assert data["label"] == "Groceries"
    assert data["group"] == "Everyday"
    assert data["categories"] == 1
    assert data["assigned"] == 500.0
    assert data["activity"] == -120.0
    assert data["available"] == 380.0
    assert data["target"] == 600.0, "target is the SCALAR amount, never the raw spec dict"
    assert data["status"] == "funded"
    assert "available_for_payment" not in data, "non-payment row omits payment fields"


def test_resolve_envelope_payment_row_carries_payment_fields(monkeypatch):
    from backend.services.mentions.resolve import _resolve_envelope

    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: _fake_table())
    data = _resolve_envelope("env_cc")
    assert data is not None
    assert data["categories"] == 0
    assert data["available_for_payment"] == 50.0
    assert data["underfunded"] == 25.0


def test_resolve_envelope_unknown_ref_is_not_found(monkeypatch):
    from backend.services.mentions.resolve import _resolve_envelope

    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: _fake_table())
    assert _resolve_envelope("env_nope") is None
    assert _resolve_envelope("") is None


def test_registry_resolve_dispatch_routes_envelope(monkeypatch):
    from backend.services.mentions.dispatch import resolve_mentions

    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: _fake_table())
    out = resolve_mentions([{"type": "envelope", "ref": "env_cc", "label": "Amex"}])
    assert len(out) == 1
    assert out[0].get("error") is None
    assert out[0]["data"]["label"] == "Amex"


# ── category surface split (same ADR) — a category with NO budget row still works ──
# The Manage-categories dialog is the category-identity drag surface; income/transfer/
# investment kinds and unlinked dormant expense categories have no budget row, so both
# search and resolve fall back to the category STORE instead of vanishing/not_found.

_STORE_ROWS = [
    {"id": "cat_food", "label": "Groceries", "kind": "expense", "color": "#22aa66"},
    {"id": "cat_equity", "label": "Equity Compensation", "kind": "income", "color": "#cc8844"},
    {"id": "split", "label": "Split", "kind": "expense"},
]


def test_search_category_includes_non_budget_row_categories(monkeypatch):
    from backend.services.mentions.providers import search_category

    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: _fake_table())
    monkeypatch.setattr("backend.services.categories.all_category_rows", lambda: _STORE_ROWS)
    rows = search_category(_query(q="equity comp"))
    assert [r["value"] for r in rows] == ["cat_equity"]
    assert rows[0]["sublabel"] == "income"


def test_search_category_dedupes_budget_rows_and_skips_split(monkeypatch):
    from backend.services.mentions.providers import search_category

    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: _fake_table())
    monkeypatch.setattr("backend.services.categories.all_category_rows", lambda: _STORE_ROWS)
    rows = search_category(_query())
    values = [r["value"] for r in rows]
    assert values.count("cat_food") == 1, "budget-linked category must not repeat via the store"
    assert "split" not in values, "the structural split sentinel is a marker, not a category"
    assert "cat_equity" in values


def test_resolve_category_store_fallback(monkeypatch):
    from backend.services.mentions.resolve import _resolve_category

    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: _fake_table())
    monkeypatch.setattr("backend.services.categories.all_category_rows", lambda: _STORE_ROWS)
    data = _resolve_category("cat_equity")
    assert data is not None
    assert data["label"] == "Equity Compensation"
    assert data["kind"] == "income"
    assert data["budgeted"] is False
    assert _resolve_category("cat_nope") is None


def test_resolve_category_refuses_the_split_sentinel(monkeypatch):
    from backend.services.mentions.resolve import _resolve_category

    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: _fake_table())
    monkeypatch.setattr("backend.services.categories.all_category_rows", lambda: _STORE_ROWS)
    assert _resolve_category("split") is None, "split is a structural marker, never resolvable"


def test_search_rows_carry_entity_display_color(monkeypatch):
    from backend.services.mentions.providers import search_category, search_envelope

    monkeypatch.setattr("backend.services.planning.budget_month", lambda m: _fake_table())
    monkeypatch.setattr("backend.services.categories.all_category_rows", lambda: _STORE_ROWS)
    env_rows = {r["id"]: r for r in search_envelope(_query())}
    assert env_rows["env_groceries"]["color"] == "#22aa66"
    assert "color" not in env_rows["env_cc"], "a colorless row omits the key"
    cat_rows = {r["id"]: r for r in search_category(_query())}
    assert cat_rows["cat_food"]["color"] == "#22aa66", "budget-row leg carries the row color"
    assert cat_rows["cat_equity"]["color"] == "#cc8844", "store leg carries the store color"
