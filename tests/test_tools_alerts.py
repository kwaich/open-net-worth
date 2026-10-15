"""AI tool-dispatch tests for backend/ai/tools/alerts.py.

Covers both the batch-pluralized tool surface (2026-07-19: set_price_alerts,
update_price_alerts, remove_price_alerts, plus get_alerts's widened ticker filter) and the
former singular names (set_price_alert, update_price_alert, remove_price_alert), which must
keep resolving as aliases — a tool name is persisted data (registry._LEGACY_TOOL_ALIASES).

Param-arity follow-up (2026-07-19d): get_alerts.ticker -> tickers,
update_price_alerts/remove_price_alerts's alert_id -> alert_ids (the old singular keys
still resolve — read-side fallback, no alias table needed for a param rename).

Isolation: db_isolated (AlertStore is DB-backed — BookPayloadTableStore), mirroring
test_alerts_service.py's fixture. Offline — no LLM, no network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.


def call(tool: str, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


@pytest.fixture
def alerts(db_isolated):
    from backend.db import alert_store as _mod

    return _mod


# ── tool registration ──────────────────────────────────────────────────────────


def test_all_alert_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {
        "get_alerts",
        "set_price_alerts",
        "update_price_alerts",
        "remove_price_alerts",
    } <= names


def test_a_former_singular_name_is_refused_not_silently_resolved():
    """A retired name is an UNKNOWN TOOL now — there is no alias to absorb it (ADR
    2026-08-25-tool-names-are-final). The failure is loud on purpose: a stored playbook on
    an old name must be migrated, not quietly kept working forever."""
    res, proposed = call("set_price_alert", ticker="AAPL", op=">=", value=200)
    assert proposed is None
    assert "unknown tool" in res.get("error", "")


def test_former_singular_names_are_aliases_not_registered_tools():

    names = {t["name"] for t in TOOL_SCHEMAS}
    renamed = {
        "set_price_alert": "set_price_alerts",
        "update_price_alert": "update_price_alerts",
        "remove_price_alert": "remove_price_alerts",
    }
    for old, new in renamed.items():
        assert old not in names, f"{old} must not be offered — only {new} is live"


# ── get_alerts ───────────────────────────────────────────────────────────────────


def test_get_alerts_lists_all(alerts):
    call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    call("set_price_alerts", ticker="MSFT", op="<=", value=300)
    res, proposed = call("get_alerts")
    assert proposed is None
    assert {a["ticker"] for a in res["alerts"]} == {"AAPL", "MSFT"}


def test_get_alerts_filters_by_one_ticker(alerts):
    call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    call("set_price_alerts", ticker="MSFT", op="<=", value=300)
    res, _ = call("get_alerts", ticker="aapl")
    assert {a["ticker"] for a in res["alerts"]} == {"AAPL"}


def test_get_alerts_filters_by_ticker_list(alerts):
    """New batch capability: ticker accepts a LIST to match any of several in one call."""
    call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    call("set_price_alerts", ticker="MSFT", op="<=", value=300)
    call("set_price_alerts", ticker="TSLA", op=">=", value=400)
    res, proposed = call("get_alerts", ticker=["aapl", "tsla"])
    assert proposed is None
    assert {a["ticker"] for a in res["alerts"]} == {"AAPL", "TSLA"}


def test_get_alerts_filters_by_tickers_plural_key(alerts):
    """The current schema key (2026-07-19d param-arity fixup): ``tickers``."""
    call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    call("set_price_alerts", ticker="MSFT", op="<=", value=300)
    res, _ = call("get_alerts", tickers="aapl")
    assert {a["ticker"] for a in res["alerts"]} == {"AAPL"}


def test_get_alerts_filters_by_tickers_list_plural_key(alerts):
    call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    call("set_price_alerts", ticker="MSFT", op="<=", value=300)
    call("set_price_alerts", ticker="TSLA", op=">=", value=400)
    res, proposed = call("get_alerts", tickers=["aapl", "tsla"])
    assert proposed is None
    assert {a["ticker"] for a in res["alerts"]} == {"AAPL", "TSLA"}


def test_get_alerts_reports_showing_and_total(alerts):
    """Regression: get_alerts used to return a bare {"alerts": [...]} with no
    `showing`/`total` — a hard project invariant (every bounded read reports both). Under
    the cap, showing == total and there is nothing to page."""
    call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    call("set_price_alerts", ticker="MSFT", op="<=", value=300)
    res, proposed = call("get_alerts")
    assert proposed is None
    assert res["showing"] == 2 and res["total"] == 2
    assert "note" not in res


def test_get_alerts_default_cap_reports_showing_and_total(alerts):
    """With more alerts than the default cap, get_alerts must truncate the page while
    still reporting the TRUE total and a way to reach the rest — never silently drop
    rows with no trace, and never claim a partial page is the whole list."""
    from backend.ai.tools.alerts import _DEFAULT_ALERT_LIMIT

    for i in range(_DEFAULT_ALERT_LIMIT + 5):
        call("set_price_alerts", ticker=f"T{i}", op=">=", value=100 + i)

    res, proposed = call("get_alerts")
    assert proposed is None
    assert res["showing"] == _DEFAULT_ALERT_LIMIT
    assert res["total"] == _DEFAULT_ALERT_LIMIT + 5
    assert len(res["alerts"]) == _DEFAULT_ALERT_LIMIT
    assert f"offset={_DEFAULT_ALERT_LIMIT}" in res["note"]


def test_get_alerts_limit_and_offset_page_through_results(alerts):
    for i in range(5):
        call("set_price_alerts", ticker=f"T{i}", op=">=", value=100 + i)

    first, _ = call("get_alerts", limit=2)
    assert first["showing"] == 2 and first["total"] == 5
    assert "offset=2" in first["note"]

    second, _ = call("get_alerts", limit=2, offset=2)
    assert second["showing"] == 2
    assert "offset=4" in second["note"]

    third, _ = call("get_alerts", limit=2, offset=4)
    assert third["showing"] == 1
    assert "note" not in third

    seen_ids = {a["id"] for page in (first, second, third) for a in page["alerts"]}
    assert len(seen_ids) == 5


# ── set_price_alerts ───────────────────────────────────────────────────────────────


def test_set_price_alerts_single(alerts):
    res, proposed = call("set_price_alerts", ticker="AAPL", op=">=", value=200, note="trim")
    assert proposed is None
    assert res["ok"] is True
    assert res["alert"]["ticker"] == "AAPL"
    assert res["alert"]["value"] == 200.0


def test_set_price_alerts_validation_error_returns_error_not_raise(alerts):
    res, _ = call("set_price_alerts", ticker="AAPL", op="==", value=200)
    assert res["ok"] is False
    assert "error" in res


def test_set_price_alerts_batch_creates_all_independently(alerts):
    res, proposed = call(
        "set_price_alerts",
        alerts=[
            {"ticker": "AAPL", "op": ">=", "value": 200},
            {"ticker": "MSFT", "op": "<=", "value": 300, "note": "stop"},
        ],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["created"] == 2
    tickers = {a["ticker"] for a in alerts.AlertStore().all()}
    assert tickers == {"AAPL", "MSFT"}


def test_set_price_alerts_batch_one_bad_item_does_not_block_the_rest(alerts):
    res, proposed = call(
        "set_price_alerts",
        alerts=[
            {"ticker": "AAPL", "op": "==", "value": 200},  # invalid op
            {"ticker": "MSFT", "op": "<=", "value": 300},
        ],
    )
    assert proposed is None
    assert res["ok"] is False
    assert res["created"] == 1
    assert "error" in res["results"][0]
    assert res["results"][1]["ok"] is True


def test_set_price_alerts_defaults_to_a_price_level_alert(alerts):
    res, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    assert res["alert"]["kind"] == "price"


def test_set_price_alerts_creates_a_percent_move_alert(alerts):
    res, _ = call("set_price_alerts", ticker="NVDA", op="<=", value=5, kind="move")
    assert res["ok"] is True
    assert res["alert"]["kind"] == "move"
    assert res["alert"]["value"] == 5.0


def test_set_price_alerts_batch_items_carry_their_own_kind(alerts):
    res, _ = call(
        "set_price_alerts",
        alerts=[
            {"ticker": "AAPL", "op": ">=", "value": 200},
            {"ticker": "NVDA", "op": ">=", "value": 3, "kind": "move"},
        ],
    )
    assert res["created"] == 2
    kinds = {a["ticker"]: a["kind"] for a in alerts.AlertStore().all()}
    assert kinds == {"AAPL": "price", "NVDA": "move"}


def test_set_price_alerts_move_percent_out_of_range_is_an_error(alerts):
    res, _ = call("set_price_alerts", ticker="NVDA", op="<=", value=150, kind="move")
    assert res["ok"] is False
    assert "move alert value" in res["error"]
    assert alerts.AlertStore().all() == []


def test_set_price_alerts_unknown_kind_is_an_error(alerts):
    res, _ = call("set_price_alerts", ticker="NVDA", op="<=", value=5, kind="volume")
    assert res["ok"] is False
    assert "kind" in res["error"]


def test_set_and_update_price_alerts_schemas_offer_kind():
    schemas = {t["name"]: t["input_schema"] for t in TOOL_SCHEMAS}
    set_item = schemas["set_price_alerts"]["properties"]["alerts"]["items"]["properties"]
    assert set(set_item["kind"]["enum"]) == {"price", "move"}
    update = schemas["update_price_alerts"]["properties"]
    assert "kind" in update
    assert "kind" in update["updates"]["items"]["properties"]


# ── update_price_alerts ────────────────────────────────────────────────────────────


def test_update_price_alerts_switches_a_price_alert_to_a_move_alert(alerts):
    created, _ = call("set_price_alerts", ticker="NVDA", op="<=", value=90)
    aid = created["alert"]["id"]
    res, _ = call("update_price_alerts", alert_ids=aid, kind="move", value=4)
    assert res["ok"] is True
    assert res["alert"]["kind"] == "move"
    assert res["alert"]["value"] == 4.0


def test_update_price_alerts_kind_change_revalidates_the_kept_value(alerts):
    """A 90 price level is not a plausible percent — switching kind alone must refuse,
    never store a move alert that can never fire."""
    created, _ = call("set_price_alerts", ticker="NVDA", op="<=", value=190)
    aid = created["alert"]["id"]
    res, _ = call("update_price_alerts", alert_ids=aid, kind="move")
    assert res["ok"] is False
    assert alerts.AlertStore().find_by_id(aid)["kind"] == "price"


def test_update_price_alerts_single(alerts):
    created, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    aid = created["alert"]["id"]
    res, proposed = call("update_price_alerts", alert_id=aid, value=210)
    assert proposed is None
    assert res["ok"] is True
    assert res["alert"]["value"] == 210.0


def test_update_price_alerts_not_found_returns_error(alerts):
    res, _ = call("update_price_alerts", alert_id="ghost", value=1)
    assert res["ok"] is False
    assert "not found" in res["error"]


def test_update_price_alerts_id_only_is_a_no_op_and_does_not_write(alerts):
    """An id-only call (no op/value/note/label) must not fake a write — the same
    no-op-fakes-success trap ``update_transactions`` had: an empty patch used to be
    routed through ``apply_alert_change``, which echoed the unchanged row back as a
    plain ``ok: true`` success, byte-indistinguishable from a real edit."""
    created, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200, note="orig")
    aid = created["alert"]["id"]
    before = alerts.AlertStore().find_by_id(aid)

    res, proposed = call("update_price_alerts", alert_ids=aid)

    assert proposed is None
    assert res["ok"] is True
    assert res["no_op"] is True
    assert "updated" not in res  # the bare no_op shape, not the {updated, results} wrapper
    assert res["alert"] == before

    after = alerts.AlertStore().find_by_id(aid)
    assert after == before  # no write happened at all


def test_update_price_alerts_id_only_nonexistent_reports_not_found_not_no_op(alerts):
    """A genuinely missing id with an empty patch is an error, not "nothing to do" —
    only an existing row with no changeable field is a no_op."""
    res, _ = call("update_price_alerts", alert_ids="ghost")
    assert res["ok"] is False
    assert "not found" in res["error"]
    assert "no_op" not in res


def test_update_price_alerts_batch_marks_empty_item_no_op_and_books_the_rest(alerts):
    """A mixed batch — one row with nothing to change, one real edit — must not abort:
    the real edit lands, the no-op row is reported honestly (rule 3: batch tools never
    abort over a no-op item)."""
    a, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    b, _ = call("set_price_alerts", ticker="MSFT", op=">=", value=300)
    aid, bid = a["alert"]["id"], b["alert"]["id"]

    res, proposed = call(
        "update_price_alerts",
        updates=[{"alert_id": aid}, {"alert_id": bid, "value": 310}],
    )

    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 1
    assert res["results"][0]["no_op"] is True
    assert res["results"][1]["ok"] is True
    assert "no_op" not in res["results"][1]

    by_id = {row["id"]: row for row in alerts.AlertStore().all()}
    assert by_id[aid]["value"] == 200.0  # untouched
    assert by_id[bid]["value"] == 310.0  # real edit landed


def test_update_price_alerts_batch_broadcasts_the_same_change(alerts):
    """Also exercises the pre-2026-07-19d singular ``alert_id`` key — must keep resolving
    as a read-side fallback."""
    a, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    b, _ = call("set_price_alerts", ticker="MSFT", op=">=", value=300)
    aid, bid = a["alert"]["id"], b["alert"]["id"]

    res, proposed = call("update_price_alerts", alert_id=[aid, bid], note="reviewed")
    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 2
    notes = {row["note"] for row in alerts.AlertStore().all()}
    assert notes == {"reviewed"}


def test_update_price_alerts_batch_accepts_the_plural_key(alerts):
    """The current schema key (2026-07-19d param-arity fixup): ``alert_ids``."""
    a, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    b, _ = call("set_price_alerts", ticker="MSFT", op=">=", value=300)
    aid, bid = a["alert"]["id"], b["alert"]["id"]

    res, proposed = call("update_price_alerts", alert_ids=[aid, bid], note="reviewed")
    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 2
    notes = {row["note"] for row in alerts.AlertStore().all()}
    assert notes == {"reviewed"}


def test_update_price_alerts_batch_different_changes_per_alert(alerts):
    a, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    b, _ = call("set_price_alerts", ticker="MSFT", op=">=", value=300)
    aid, bid = a["alert"]["id"], b["alert"]["id"]

    res, proposed = call(
        "update_price_alerts",
        updates=[{"alert_id": aid, "value": 210}, {"alert_id": bid, "value": 310}],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 2
    by_id = {row["id"]: row for row in alerts.AlertStore().all()}
    assert by_id[aid]["value"] == 210.0
    assert by_id[bid]["value"] == 310.0


def test_update_price_alerts_batch_one_not_found_does_not_block_the_rest(alerts):
    a, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    aid = a["alert"]["id"]

    res, proposed = call(
        "update_price_alerts",
        updates=[{"alert_id": "ghost", "value": 1}, {"alert_id": aid, "value": 210}],
    )
    assert proposed is None
    assert res["ok"] is False
    assert res["updated"] == 1
    assert "error" in res["results"][0]
    assert res["results"][1]["ok"] is True


# ── remove_price_alerts ────────────────────────────────────────────────────────────


def test_remove_price_alerts_single(alerts):
    """Also exercises the pre-2026-07-19d singular ``alert_id`` key — must keep resolving
    as a read-side fallback."""
    created, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    aid = created["alert"]["id"]
    res, proposed = call("remove_price_alerts", alert_id=aid)
    assert proposed is None
    assert res["ok"] is True
    assert res["deleted"] is True
    assert alerts.AlertStore().all() == []


def test_remove_price_alerts_single_via_plural_key(alerts):
    """The current schema key (2026-07-19d param-arity fixup): ``alert_ids``."""
    created, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    aid = created["alert"]["id"]
    res, proposed = call("remove_price_alerts", alert_ids=aid)
    assert proposed is None
    assert res["ok"] is True
    assert res["deleted"] is True
    assert alerts.AlertStore().all() == []


def test_remove_price_alerts_not_found_returns_error(alerts):
    res, _ = call("remove_price_alerts", alert_id="ghost")
    assert res["ok"] is False
    assert "not found" in res["error"]


def test_remove_price_alerts_batch_deletes_several_at_once(alerts):
    a, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    b, _ = call("set_price_alerts", ticker="MSFT", op=">=", value=300)
    res, proposed = call("remove_price_alerts", alert_id=[a["alert"]["id"], b["alert"]["id"]])
    assert proposed is None
    assert res["ok"] is True
    assert res["deleted_count"] == 2
    assert alerts.AlertStore().all() == []


def test_remove_price_alerts_batch_via_plural_key(alerts):
    a, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    b, _ = call("set_price_alerts", ticker="MSFT", op=">=", value=300)
    res, proposed = call("remove_price_alerts", alert_ids=[a["alert"]["id"], b["alert"]["id"]])
    assert proposed is None
    assert res["ok"] is True
    assert res["deleted_count"] == 2
    assert alerts.AlertStore().all() == []


def test_remove_price_alerts_batch_one_not_found_does_not_block_the_rest(alerts):
    a, _ = call("set_price_alerts", ticker="AAPL", op=">=", value=200)
    aid = a["alert"]["id"]

    res, proposed = call("remove_price_alerts", alert_id=["ghost", aid])
    assert proposed is None
    assert res["ok"] is False
    assert res["deleted_count"] == 1
    assert "error" in res["results"][0]
    assert res["results"][1]["ok"] is True
    assert alerts.AlertStore().all() == []


# ── mutates domain tag (frontend freshness refresh) ─────────────────────────────────


@pytest.mark.parametrize("name", ["set_price_alerts", "update_price_alerts", "remove_price_alerts"])
def test_write_tools_declare_a_mutates_domain(name):
    from backend.ai.tools.registry import mutated_domains

    assert mutated_domains(name) == ("alerts",)
