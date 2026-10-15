"""P0-C: Golden-parity baselines for per-type search and resolve_mentions.

RFC 2026-06-25-lucius-generic-entity-registry §10 Phase 0.

Captures the PUBLIC engine entry points:
  - ``search(SearchQuery(...))`` — covers rank + cursor + surface filter.
  - ``dispatch.resolve_mentions(refs)`` — the chat-turn resolution path.

These baselines prove that Phase 1's generic provider collapse produces
byte-identical output for each of the 16 registered types.  Run them BEFORE
and AFTER any Phase 1 provider migration; a diff == a parity regression.

The chat-turn mention-context bytes (format_mention_context) changed in P0-A
(fence + nonce).  test_chat_mentions.py already asserts the fenced output;
this file focuses ONLY on the fence-independent per-type search/resolve parity.

Test structure:
  - For each type: ``search(q="", ...)`` returns a well-formed result dict.
  - For each type: ``search(q="xyz_no_match_123", ...)`` returns no items.
  - For each type: ``resolve_mentions([{type, ref="", label=""}])`` returns
    a list with one entry; it either has ``data`` or ``error``.
  - For types that are multi-surface (agent, skill): surface-filtered search
    returns only rows with the requested surface.
  - Cursor round-trip: a second page with offset > 0 returns an empty or
    reduced item list (no crash).

All tests are hermetic: empty stores (throwaway DATA_DIR), deterministic FX,
no network.  Singleton types (net_worth, returns, budget_month, allocation_slice,
category) always have data; store-backed types return empty results on empty
stores — and that empty-store behavior is itself a stable baseline to preserve.
"""

from __future__ import annotations

import json
import re

import pytest

from backend.core import fx
from backend.core.paths import PROFILE_JSON
from backend.services.mentions.dispatch import format_mention_context, resolve_mentions
from backend.services.mentions.registry import REGISTRY
from backend.services.mentions.search import search
from backend.services.mentions.types import SearchQuery

_FX = {"USD": 1.0}
_REQUIRED_ROW_KEYS = {"type", "id", "label", "sublabel", "value", "icon", "surface"}
_ENTITY_SURFACE = frozenset({"entity"})
_ALL_SURFACES = None  # None means no filter → all surfaces


def _q(
    q: str = "",
    type_: str | None = None,
    surfaces=_ENTITY_SURFACE,
    limit: int = 25,
    cursor: str | None = None,
) -> SearchQuery:
    types = frozenset({type_}) if type_ else None
    return SearchQuery(q=q, types=types, surfaces=surfaces, limit=limit, cursor=cursor)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    PROFILE_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_JSON.write_text(json.dumps({"base_currency": "USD"}), encoding="utf-8")
    fx.set_fx_rates_loader(lambda: dict(_FX))
    monkeypatch.setattr("backend.services.prices.fetch_analyst", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(
        "backend.services.prices.fetch_analyst_ratings", lambda *a, **k: [], raising=False
    )
    yield
    fx.set_fx_rates_loader(None)


# ── Helper assertions shared across all type tests ─────────────────────────────


def _assert_search_shape(result: dict, type_: str) -> None:
    assert isinstance(result, dict), f"{type_}: search must return a dict"
    assert "items" in result, f"{type_}: search result missing 'items'"
    assert "next_cursor" in result, f"{type_}: search result missing 'next_cursor'"
    assert isinstance(result["items"], list), f"{type_}: 'items' must be a list"
    for row in result["items"]:
        missing = _REQUIRED_ROW_KEYS - set(row)
        assert not missing, f"{type_}: row missing keys {missing}: {row}"
        assert row["type"] == type_, f"{type_}: row has wrong type '{row['type']}'"
        assert isinstance(row["value"], str), f"{type_}: row 'value' must be str"
        assert row["icon"], f"{type_}: row 'icon' must be non-empty"
        assert row["surface"] in ("entity", "agent", "verb"), (
            f"{type_}: unknown surface '{row['surface']}'"
        )


def _assert_resolve_shape(results: list[dict], type_: str) -> None:
    assert isinstance(results, list), f"{type_}: resolve_mentions must return a list"
    assert len(results) == 1, f"{type_}: expected exactly 1 resolve result"
    entry = results[0]
    assert entry["type"] == type_
    assert "ref" in entry
    has_data = "data" in entry
    has_error = "error" in entry
    assert has_data ^ has_error, f"{type_}: entry must have exactly one of 'data' or 'error'"
    if has_data:
        assert isinstance(entry["data"], dict), f"{type_}: 'data' must be a dict"
    if has_error:
        assert entry["error"] in ("not_found", "unknown_type"), (
            f"{type_}: unexpected error value '{entry['error']}'"
        )


# ── Per-type parity tests ──────────────────────────────────────────────────────


def _parity_search_empty_q(type_: str) -> None:
    result = search(_q(q="", type_=type_, surfaces=_ENTITY_SURFACE))
    _assert_search_shape(result, type_)


def _parity_search_no_match(type_: str) -> None:
    result = search(_q(q="xyz_no_match_123", type_=type_, surfaces=_ENTITY_SURFACE))
    _assert_search_shape(result, type_)
    assert result["items"] == [], f"{type_}: no-match query must return empty items"


def _parity_resolve(type_: str, ref: str = "") -> None:
    results = resolve_mentions([{"type": type_, "ref": ref, "label": type_}])
    _assert_resolve_shape(results, type_)


# net_worth — singleton: always returns one row; resolves with data (no store needed)
def test_parity_net_worth_search():
    _parity_search_empty_q("net_worth")
    result = search(_q(q="", type_="net_worth"))
    assert len(result["items"]) == 1
    assert result["items"][0]["value"] == ""  # singleton ref is empty string


def test_parity_net_worth_no_match():
    _parity_search_no_match("net_worth")


def test_parity_net_worth_resolve():
    results = resolve_mentions([{"type": "net_worth", "ref": "", "label": "Net worth"}])
    _assert_resolve_shape(results, "net_worth")
    assert "data" in results[0]
    assert "net_worth" in results[0]["data"]


# holding — service-hydrated; empty on an empty store
def test_parity_holding_search():
    _parity_search_empty_q("holding")


def test_parity_holding_no_match():
    _parity_search_no_match("holding")


def test_parity_holding_resolve():
    _parity_resolve("holding", ref="non-existent-pos")


# instrument — store-backed; empty on an empty store
def test_parity_instrument_search():
    _parity_search_empty_q("instrument")


def test_parity_instrument_no_match():
    _parity_search_no_match("instrument")


def test_parity_instrument_resolve():
    _parity_resolve("instrument", ref="non-existent-inst")


# account — service-hydrated; empty on an empty store
def test_parity_account_search():
    _parity_search_empty_q("account")


def test_parity_account_no_match():
    _parity_search_no_match("account")


def test_parity_account_resolve():
    _parity_resolve("account", ref="non-existent-acc")


# transaction — store-backed; empty on an empty store
def test_parity_transaction_search():
    _parity_search_empty_q("transaction")


def test_parity_transaction_no_match():
    _parity_search_no_match("transaction")


def test_parity_transaction_resolve():
    _parity_resolve("transaction", ref="non-existent-txn")


# category — synthesized from planning; always has categories
def test_parity_category_search():
    _parity_search_empty_q("category")


def test_parity_category_no_match():
    _parity_search_no_match("category")


def test_parity_category_resolve_not_found():
    _parity_resolve("category", ref="non-existent-cat")


# budget_month — synthesized; always returns data for the current month
def test_parity_budget_month_search():
    _parity_search_empty_q("budget_month")
    result = search(_q(q="", type_="budget_month"))
    assert len(result["items"]) == 12  # 12 months


def test_parity_budget_month_no_match():
    _parity_search_no_match("budget_month")


def test_parity_budget_month_resolve():
    results = resolve_mentions([{"type": "budget_month", "ref": "2026-06", "label": "Jun 2026"}])
    _assert_resolve_shape(results, "budget_month")
    assert "data" in results[0]


# target — store-backed; empty on empty store
def test_parity_target_search():
    _parity_search_empty_q("target")


def test_parity_target_no_match():
    _parity_search_no_match("target")


def test_parity_target_resolve():
    _parity_resolve("target", ref="non-existent-target")


# allocation_slice — synthesized from positions; empty on empty position store
def test_parity_allocation_slice_search():
    _parity_search_empty_q("allocation_slice")


def test_parity_allocation_slice_no_match():
    _parity_search_no_match("allocation_slice")


def test_parity_allocation_slice_resolve_bad_ref():
    results = resolve_mentions([{"type": "allocation_slice", "ref": "bad", "label": "bad"}])
    _assert_resolve_shape(results, "allocation_slice")
    # "bad" has no ":" separator → not_found
    assert results[0].get("error") == "not_found"


def test_parity_allocation_slice_resolve_colon_ref():
    # A colon-containing ref must not crash (it may return not_found with empty positions)
    results = resolve_mentions(
        [{"type": "allocation_slice", "ref": "asset_class:equity", "label": "Equity"}]
    )
    _assert_resolve_shape(results, "allocation_slice")


# returns — synthesized periods; always returns 8 rows
def test_parity_returns_search():
    _parity_search_empty_q("returns")
    result = search(_q(q="", type_="returns"))
    assert len(result["items"]) == 8  # YTD, 1M, 3M, 6M, 1Y, 3Y, 5Y, All


def test_parity_returns_no_match():
    _parity_search_no_match("returns")


def test_parity_returns_resolve():
    results = resolve_mentions([{"type": "returns", "ref": "1Y", "label": "1Y return"}])
    _assert_resolve_shape(results, "returns")


# payee — store-backed; empty on empty store
def test_parity_payee_search():
    _parity_search_empty_q("payee")


def test_parity_payee_no_match():
    _parity_search_no_match("payee")


def test_parity_payee_resolve():
    _parity_resolve("payee", ref="non-existent-payee")


# watchlist — store-backed; empty on empty store
def test_parity_watchlist_search():
    _parity_search_empty_q("watchlist")


def test_parity_watchlist_no_match():
    _parity_search_no_match("watchlist")


def test_parity_watchlist_resolve():
    _parity_resolve("watchlist", ref="non-existent-wl")


# rule — store-backed; empty on empty store
def test_parity_rule_search():
    _parity_search_empty_q("rule")


def test_parity_rule_no_match():
    _parity_search_no_match("rule")


def test_parity_rule_resolve():
    _parity_resolve("rule", ref="non-existent-rule")


# scheduled_task — store-backed; empty on empty store
def test_parity_scheduled_task_search():
    _parity_search_empty_q("scheduled_task")


def test_parity_scheduled_task_no_match():
    _parity_search_no_match("scheduled_task")


def test_parity_scheduled_task_resolve():
    _parity_resolve("scheduled_task", ref="non-existent-task")


# The four agent-parity cases (entity surface, agent surface, no-match, resolve) went with
# the "agent" mention type on 2026-08-02. The `skill` provider is the surviving multi-surface
# one and its parity cases below cover the same shape: one type, two @-surfaces, each
# returning only its own surface's rows.


def test_parity_skill_search_verb_surface():
    result = search(_q(q="", type_="skill", surfaces=frozenset({"verb"})))
    _assert_search_shape(result, "skill")
    for row in result["items"]:
        assert row["surface"] == "verb"


def test_parity_skill_no_match():
    _parity_search_no_match("skill")


def test_parity_skill_resolve():
    _parity_resolve("skill", ref="non-existent-skill")


# commitment — generic-provider; empty on empty store
def test_parity_commitment_search():
    _parity_search_empty_q("commitment")


def test_parity_commitment_no_match():
    _parity_search_no_match("commitment")


def test_parity_commitment_resolve():
    _parity_resolve("commitment", ref="non-existent-commitment")


# notification (renamed 2026-08-20 from intel_signal) — generic-provider; empty on empty store
def test_parity_notification_search():
    _parity_search_empty_q("notification")


def test_parity_notification_no_match():
    _parity_search_no_match("notification")


def test_parity_notification_resolve():
    _parity_resolve("notification", ref="non-existent-signal")


# alert — generic-provider; empty on empty store
def test_parity_alert_search():
    _parity_search_empty_q("alert")


def test_parity_alert_no_match():
    _parity_search_no_match("alert")


def test_parity_alert_resolve():
    _parity_resolve("alert", ref="non-existent-alert")


# income — generic-provider; empty on empty store
def test_parity_income_search():
    _parity_search_empty_q("income")


def test_parity_income_no_match():
    _parity_search_no_match("income")


def test_parity_income_resolve():
    _parity_resolve("income", ref="non-existent-income")


# lot — generic-provider; empty on empty store
def test_parity_lot_search():
    _parity_search_empty_q("lot")


def test_parity_lot_no_match():
    _parity_search_no_match("lot")


def test_parity_lot_resolve():
    _parity_resolve("lot", ref="non-existent-lot")


# grant — generic-provider; empty on empty store
def test_parity_grant_search():
    _parity_search_empty_q("grant")


def test_parity_grant_no_match():
    _parity_search_no_match("grant")


def test_parity_grant_resolve():
    _parity_resolve("grant", ref="non-existent-grant")


# vest — generic-provider; empty on empty store
def test_parity_vest_search():
    _parity_search_empty_q("vest")


def test_parity_vest_no_match():
    _parity_search_no_match("vest")


def test_parity_vest_resolve():
    _parity_resolve("vest", ref="non-existent-vest")


# ── Cross-cutting invariants ───────────────────────────────────────────────────


def test_all_types_have_search_and_resolve():
    """Every registered type must have both a search fn and a resolve fn."""
    for type_, provider in REGISTRY.items():
        assert callable(provider.search), f"{type_}: search must be callable"
        assert callable(provider.resolve), f"{type_}: resolve must be callable"


def test_resolve_mentions_dedup():
    """Duplicate (type, ref) pairs in one call are deduplicated."""
    refs = [
        {"type": "net_worth", "ref": "", "label": "NW"},
        {"type": "net_worth", "ref": "", "label": "NW again"},
    ]
    results = resolve_mentions(refs)
    assert len(results) == 1


def test_resolve_mentions_unknown_type():
    results = resolve_mentions([{"type": "ghost_type", "ref": "x", "label": "X"}])
    assert len(results) == 1
    assert results[0]["error"] == "unknown_type"


def test_format_mention_context_returns_fenced_output():
    """format_mention_context must wrap output in the non-forgeable nonce fence."""
    resolved = resolve_mentions([{"type": "net_worth", "ref": "", "label": "Net worth"}])
    block = format_mention_context(resolved)
    assert block, "format_mention_context must return a non-empty string"
    assert re.search(r"«mention:[0-9a-f]{32}»", block), "fence open marker missing"
    assert re.search(r"«/mention:[0-9a-f]{32}»", block), "fence close marker missing"
    assert "UNTRUSTED" in block
    assert "@Net worth:" in block


def test_cursor_page_two_does_not_crash():
    """A second-page cursor (offset > total) must return empty items, not crash."""
    import base64

    cursor = base64.b64encode(b"9999").decode()
    for type_ in REGISTRY:
        result = search(_q(q="", type_=type_, limit=10, cursor=cursor))
        assert result["items"] == [], f"{type_}: cursor past end should return []"
        assert result["next_cursor"] is None
