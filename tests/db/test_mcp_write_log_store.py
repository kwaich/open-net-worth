"""McpWriteLogStore — DB-CRUD round-trip + the capped/descending-read domain-parity case
(plan 4c task 1, Bucket A). Endpoint-level behavior already covered by tests/test_mcp.py;
this is the store's OWN coverage."""

from __future__ import annotations

from backend.db.mcp_write_log_store import MAX_WRITE_LOG, McpWriteLogStore


def test_append_returns_newest_first(db_isolated):
    store = McpWriteLogStore()
    first = store.append({"tool": "add_positions", "result_ok": True})
    second = store.append({"tool": "delete_transactions", "result_ok": False})

    rows = store.recent()
    assert [r["id"] for r in rows] == [second["id"], first["id"]]


def test_crud_round_trip_preserves_money_and_domain_fields(db_isolated):
    store = McpWriteLogStore()
    row = store.append(
        {
            "surface": "server:s1",
            "tool": "add_positions",
            "domains": ["positions"],
            "args_summary": "AAPL x5",
            "base_amount": 1234.56,
            "checkpoint_id": "ckpt_abc",
            "result_ok": True,
        }
    )
    assert row["base_amount"] == 1234.56  # raw payload value — never touched by the store

    fetched = store.recent(1)[0]
    assert fetched["tool"] == "add_positions"
    assert fetched["domains"] == ["positions"]
    assert fetched["checkpoint_id"] == "ckpt_abc"


def test_recent_respects_limit(db_isolated):
    store = McpWriteLogStore()
    for i in range(5):
        store.append({"tool": f"t{i}"})

    assert len(store.recent(2)) == 2
    assert store.recent(2)[0]["tool"] == "t4"


def test_append_caps_at_max_write_log_dropping_oldest(db_isolated):
    store = McpWriteLogStore()
    for i in range(MAX_WRITE_LOG + 10):
        store.append({"tool": f"t{i}"})

    rows = store.recent(MAX_WRITE_LOG + 50)
    assert len(rows) == MAX_WRITE_LOG
    assert rows[0]["tool"] == f"t{MAX_WRITE_LOG + 9}"
    assert not any(r["tool"] == "t0" for r in rows)
