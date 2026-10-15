"""Tests for the multi-provider batch seam (Feature B — RFC round-4 cost optimization).

Covers:
- Seam registration + fallback (no provider registered → sync fallback returns correct shape)
- BatchRequest extra_params field
- BatchPending sentinel
- Ledger CRUD + status transitions
- BatchLedgerStore.is_expired
- cost_tracker.record batch=True flag + 50% pricing
- Intel scan falls back to sync when no provider registered (smoke test)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Core seam: registration + sync fallback
# ---------------------------------------------------------------------------


def test_seam_no_provider_raises_on_complete():
    """When no LLM provider is registered, complete() raises LLMUnavailable."""
    from backend.core import llm_seam

    orig_complete = llm_seam._complete
    try:
        llm_seam._complete = None
        with __import__("pytest").raises(llm_seam.LLMUnavailable):
            asyncio.run(llm_seam.complete("hello"))
    finally:
        llm_seam._complete = orig_complete


def test_seam_sync_fallback_returns_results():
    """When no batch provider registered, submit_batch uses the sync fallback."""
    from backend.core import llm_seam

    orig_submit = llm_seam._submit_batch
    orig_collect = llm_seam._collect_batch
    orig_complete = llm_seam._complete

    async def _fake_complete(prompt, *, system="", model="", max_tokens=2048, source="complete"):
        return f"reply:{prompt}"

    async def _run():
        llm_seam._submit_batch = None
        llm_seam._collect_batch = None
        llm_seam._complete = _fake_complete

        reqs = [
            llm_seam.BatchRequest(custom_id="a", prompt="hello"),
            llm_seam.BatchRequest(custom_id="b", prompt="world"),
        ]
        handle = await llm_seam.submit_batch("test", reqs)
        assert handle.startswith(llm_seam._SYNC_PREFIX)

        results = await llm_seam.collect_batch(handle)
        assert not isinstance(results, llm_seam.BatchPending)
        by_id = {r.custom_id: r.text for r in results}
        assert by_id["a"] == "reply:hello"
        assert by_id["b"] == "reply:world"

    try:
        asyncio.run(_run())
    finally:
        llm_seam._submit_batch = orig_submit
        llm_seam._collect_batch = orig_collect
        llm_seam._complete = orig_complete


def test_seam_sync_fallback_handles_error():
    """A failing complete() in sync fallback sets error field on the result."""
    from backend.core import llm_seam

    orig_submit = llm_seam._submit_batch
    orig_collect = llm_seam._collect_batch
    orig_complete = llm_seam._complete

    async def _fake_complete(prompt, **_):
        raise RuntimeError("provider down")

    async def _run():
        llm_seam._submit_batch = None
        llm_seam._collect_batch = None
        llm_seam._complete = _fake_complete

        reqs = [llm_seam.BatchRequest(custom_id="x", prompt="test")]
        handle = await llm_seam.submit_batch("test", reqs)
        results = await llm_seam.collect_batch(handle)
        assert len(results) == 1
        assert results[0].custom_id == "x"
        assert results[0].text is None
        assert "provider down" in (results[0].error or "")

    try:
        asyncio.run(_run())
    finally:
        llm_seam._submit_batch = orig_submit
        llm_seam._collect_batch = orig_collect
        llm_seam._complete = orig_complete


def test_seam_sync_fallback_honors_output_schema():
    """2026-07-22 regression: a request carrying ``output_schema`` must go through
    ``complete_json`` (forced ``response_format``) in the sync fallback, not bare
    ``complete()`` — the gap that let intel_agent's forced write_stories schema get
    silently dropped, so an unconstrained GLM/OpenRouter reply came back as markdown
    prose instead of ``{"stories": [...]}`` and every scan failed to parse."""
    from backend.core import llm_seam

    orig_submit = llm_seam._submit_batch
    orig_collect = llm_seam._collect_batch
    orig_complete = llm_seam._complete

    seen_kwargs: dict = {}

    async def _fake_complete(
        prompt,
        *,
        system="",
        model="",
        max_tokens=2048,
        source="complete",
        response_format=None,
        effort="",
        images=None,
    ):
        seen_kwargs["response_format"] = response_format
        return json.dumps({"stories": [{"headline": "ok"}]})

    async def _run():
        llm_seam._submit_batch = None
        llm_seam._collect_batch = None
        llm_seam._complete = _fake_complete

        schema = llm_seam.BatchOutputSchema(
            name="write_stories", description="", schema={"type": "object"}
        )
        reqs = [llm_seam.BatchRequest(custom_id="a", prompt="scan for news", output_schema=schema)]
        handle = await llm_seam.submit_batch("intel", reqs)
        results = await llm_seam.collect_batch(handle)
        assert results[0].error is None
        parsed = json.loads(results[0].text)
        assert parsed == {"stories": [{"headline": "ok"}]}
        # response_format must actually have been forced through to the provider call.
        assert seen_kwargs["response_format"] is not None

    try:
        asyncio.run(_run())
    finally:
        llm_seam._submit_batch = orig_submit
        llm_seam._collect_batch = orig_collect
        llm_seam._complete = orig_complete


def test_seam_sync_fallback_output_schema_survives_prose_reply():
    """When the model ignores the forced format and replies with prose, the sync
    fallback must surface an honest empty result (None text) — never the raw prose
    string, which would blow up json.loads downstream in _parse_batch_result."""
    from backend.core import llm_seam

    orig_submit = llm_seam._submit_batch
    orig_collect = llm_seam._collect_batch
    orig_complete = llm_seam._complete

    async def _fake_complete(
        prompt,
        *,
        system="",
        model="",
        max_tokens=2048,
        source="complete",
        response_format=None,
        effort="",
        images=None,
    ):
        return "# Lucius Scan — 2026-07-22\n\nSome markdown report, not JSON."

    async def _run():
        llm_seam._submit_batch = None
        llm_seam._collect_batch = None
        llm_seam._complete = _fake_complete

        schema = llm_seam.BatchOutputSchema(
            name="write_stories", description="", schema={"type": "object"}
        )
        reqs = [llm_seam.BatchRequest(custom_id="a", prompt="scan for news", output_schema=schema)]
        handle = await llm_seam.submit_batch("intel", reqs)
        results = await llm_seam.collect_batch(handle)
        assert results[0].error is None
        assert results[0].text is None

    try:
        asyncio.run(_run())
    finally:
        llm_seam._submit_batch = orig_submit
        llm_seam._collect_batch = orig_collect
        llm_seam._complete = orig_complete


def test_seam_collect_unknown_handle_returns_pending():
    """collect_batch on an unknown handle returns BatchPending (not an exception)."""
    from backend.core import llm_seam

    orig_collect = llm_seam._collect_batch

    async def _run():
        llm_seam._collect_batch = None
        result = await llm_seam.collect_batch("sync:nonexistent-handle-xyz")
        assert isinstance(result, llm_seam.BatchPending)

    try:
        asyncio.run(_run())
    finally:
        llm_seam._collect_batch = orig_collect


def test_batch_request_extra_params_default_empty():
    """BatchRequest.extra_params defaults to an empty dict."""
    from backend.core.llm_seam import BatchRequest

    req = BatchRequest(custom_id="x", prompt="p")
    assert req.extra_params == {}


def test_batch_request_extra_params_set():
    """BatchRequest.extra_params carries provider-specific fields."""
    from backend.core.llm_seam import BatchRequest

    req = BatchRequest(
        custom_id="x",
        prompt="p",
        extra_params={"tools": [{"name": "foo"}], "tool_choice": {"type": "auto"}},
    )
    assert req.extra_params["tools"][0]["name"] == "foo"


# ---------------------------------------------------------------------------
# BatchLedgerStore CRUD + status transitions
# ---------------------------------------------------------------------------


def test_ledger_add_and_find(db_isolated):
    from backend.db.batch_ledger_store import BatchLedgerStore

    store = BatchLedgerStore()
    row = store.add(
        handle="batch-001",
        task="intel",
        transport="anthropic",
        source="intel_scan",
        model="claude-haiku-4-5",
        custom_ids=["c1"],
    )
    assert row["handle"] == "batch-001"
    assert row["status"] == "pending"

    found = store.find_by_id("batch-001")
    assert found is not None
    assert found["task"] == "intel"


def test_ledger_pending_filter(db_isolated):
    from backend.db.batch_ledger_store import BatchLedgerStore

    store = BatchLedgerStore()
    store.add("h1", "intel", "anthropic", "intel_scan", "m", ["c1"])
    store.add("h2", "intel", "anthropic", "intel_scan", "m", ["c2"])
    store.mark_done("h1")

    pending = store.pending()
    assert len(pending) == 1
    assert pending[0]["handle"] == "h2"


def test_ledger_status_transitions(db_isolated):
    from backend.db.batch_ledger_store import BatchLedgerStore

    store = BatchLedgerStore()
    store.add("h1", "intel", "anthropic", "s", "m", ["c"])
    store.mark_done("h1")
    assert store.find_by_id("h1")["status"] == "done"

    store.add("h2", "intel", "anthropic", "s", "m", ["c"])
    store.mark_failed("h2")
    assert store.find_by_id("h2")["status"] == "failed"

    store.add("h3", "intel", "anthropic", "s", "m", ["c"])
    store.mark_expired("h3")
    assert store.find_by_id("h3")["status"] == "expired"


def test_ledger_mark_polled_increments(db_isolated):
    from backend.db.batch_ledger_store import BatchLedgerStore

    store = BatchLedgerStore()
    store.add("h1", "intel", "anthropic", "s", "m", ["c"])
    store.mark_polled("h1")
    row = store.find_by_id("h1")
    assert row["poll_count"] == 1
    assert row["last_polled_at"] is not None

    store.mark_polled("h1")
    assert store.find_by_id("h1")["poll_count"] == 2


def test_ledger_is_expired_old_row():
    from backend.db.batch_ledger_store import BatchLedgerStore

    store = BatchLedgerStore.__new__(BatchLedgerStore)
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    row = {
        "handle": "h-old",
        "submitted_at": old_ts,
        "status": "pending",
    }
    assert store.is_expired(row) is True


def test_ledger_is_expired_fresh_row():
    from backend.db.batch_ledger_store import BatchLedgerStore

    store = BatchLedgerStore.__new__(BatchLedgerStore)
    fresh_ts = datetime.now(timezone.utc).isoformat()
    row = {
        "handle": "h-new",
        "submitted_at": fresh_ts,
        "status": "pending",
    }
    assert store.is_expired(row) is False


def test_ledger_metadata_stored(db_isolated):
    from backend.db.batch_ledger_store import BatchLedgerStore

    store = BatchLedgerStore()
    store.add(
        "h1",
        "intel",
        "anthropic",
        "s",
        "m",
        ["c"],
        metadata={"url_to_date": {"https://x.com": "2026-01-01"}},
    )
    row = store.find_by_id("h1")
    assert row["metadata"]["url_to_date"]["https://x.com"] == "2026-01-01"


def test_ledger_persists_across_instances(db_isolated):
    """Rows survive a store re-instantiation (DB-backed persistence — both instances
    resolve the SAME db_isolated-redirected engine, so a fresh instance sees prior writes)."""
    from backend.db.batch_ledger_store import BatchLedgerStore

    BatchLedgerStore().add("h-persist", "intel", "anthropic", "s", "m", ["c"])

    rows = BatchLedgerStore().all()
    assert any(r["handle"] == "h-persist" for r in rows)


# ---------------------------------------------------------------------------
# cost_tracker.record batch=True flag + 50% pricing
# ---------------------------------------------------------------------------


def test_cost_tracker_batch_flag_written():
    """batch=True is stored in the cost record.

    cost_log/cost_log_daily are DB-backed now (WAVE 5B, JSON->DB; CostLogStore/
    CostLogDailyStore) — no more COST_LOG_JSON/COST_LOG_DAILY_JSON file to patch; read
    back via the store, and tests/conftest.py's autouse per-test table clear already
    isolates it.
    """
    from backend.ai.cost_tracker import record
    from backend.db.cost_log_store import CostLogStore

    record("batch", "claude-haiku-4-5", 100, 50, batch=True)

    records = CostLogStore().all()
    assert len(records) == 1
    assert records[0].get("batch") is True


def test_cost_tracker_batch_flag_not_written_when_false():
    """batch field is absent when batch=False (default)."""
    from backend.ai.cost_tracker import record
    from backend.db.cost_log_store import CostLogStore

    record("chat", "claude-haiku-4-5", 100, 50)

    records = CostLogStore().all()
    assert "batch" not in records[0]


def test_cost_tracker_batch_halves_cost(tmp_path):
    """batch=True applies the 50% discount when cost_usd is not pre-provided."""
    log_path = tmp_path / "cost_log.json"
    daily_path = tmp_path / "cost_log_daily.json"

    with (
        patch("backend.paths.COST_LOG_JSON", log_path),
        patch("backend.paths.COST_LOG_DAILY_JSON", daily_path),
    ):
        from backend.ai.cost_tracker import record

        cost_regular = record("chat", "claude-haiku-4-5", 1_000_000, 0, batch=False)
        cost_batch = record("batch", "claude-haiku-4-5", 1_000_000, 0, batch=True)

    # batch cost must be exactly half of regular cost
    assert cost_batch == pytest.approx(cost_regular * 0.5, rel=1e-6)


def test_cost_tracker_batch_no_discount_when_cost_usd_provided(tmp_path):
    """When cost_usd is pre-computed by the caller, batch flag does NOT halve it again."""
    log_path = tmp_path / "cost_log.json"
    daily_path = tmp_path / "cost_log_daily.json"

    with (
        patch("backend.paths.COST_LOG_JSON", log_path),
        patch("backend.paths.COST_LOG_DAILY_JSON", daily_path),
    ):
        from backend.ai.cost_tracker import record

        cost = record("batch", "claude-haiku-4-5", 100, 50, cost_usd=0.42, batch=True)

    assert cost == 0.42  # unchanged


# ---------------------------------------------------------------------------
# Blocker 1: custom_id round-trip — submit → ledger → reap matches
# ---------------------------------------------------------------------------


def test_intel_submit_stores_real_custom_id(tmp_path, db_isolated):
    """_submit_and_poll returns the real custom_id; it must be stored in the ledger row
    so the reap sweep can match results.  Regression test for the f'intel-{handle}' bug."""
    import asyncio

    from backend.core.llm_seam import BatchRequest, BatchResult

    captured_requests: list[BatchRequest] = []
    stored_handle = "provider-batch-abc"

    async def _fake_submit(task, requests):
        captured_requests.extend(requests)
        return stored_handle

    async def _fake_collect(handle):
        # Return immediately with results keyed on the REAL custom_id (not a derived one).
        assert handle == stored_handle
        cid = captured_requests[0].custom_id
        payload = json.dumps(
            {
                "stories": [
                    {
                        "type": "asset",
                        "label": "Test Inc",
                        "status": "monitoring",
                        "summary": "all good",
                        "new_timeline_event": {
                            "date": "2026-06-11",
                            "title": "check",
                            "summary": "ok",
                            "sources": [],
                        },
                    }
                ]
            }
        )
        return [BatchResult(custom_id=cid, text=payload)]

    from backend.core import llm_seam as _seam

    orig_submit = _seam._submit_batch
    orig_collect = _seam._collect_batch

    try:
        _seam._submit_batch = _fake_submit
        _seam._collect_batch = _fake_collect

        from backend.monitoring.intel_agent import _submit_and_poll

        async def _run():
            stories_data, pending_handle, custom_id, synth_error = await _submit_and_poll(
                prompt="test",
                system="",
                model="",
                max_tokens=100,
                source="test",
                task="intel",
                url_to_date={},
            )
            # Resolved in-run (fake collect returns immediately), no pending_handle.
            assert pending_handle is None
            assert custom_id.startswith("intel-")
            assert stories_data is not None and len(stories_data) == 1
            assert synth_error is None

        asyncio.run(_run())

        # Simulate the parked path: check that the custom_id stored is the real one,
        # not a derivative of the handle.
        real_cid = captured_requests[0].custom_id
        assert real_cid.startswith("intel-")
        assert real_cid != f"intel-{stored_handle}"

        # Verify reap: apply_intel_batch_results uses the stored custom_id to find result.
        # IntelStoriesStore is DB-backed now (plan 4d task 2) — db_isolated (already a
        # parameter above) redirects it to a fresh, empty database; no more per-store
        # INTEL_SIGNALS_JSON to monkeypatch.
        from backend.monitoring.intel_agent import apply_intel_batch_results

        fake_results = [
            BatchResult(
                custom_id=real_cid,
                text=json.dumps(
                    {
                        "stories": [
                            {
                                "type": "asset",
                                "label": "Reap Corp",
                                "status": "monitoring",
                                "summary": "reaped",
                                "new_timeline_event": {
                                    "date": "2026-06-11",
                                    "title": "reap",
                                    "summary": "ok",
                                    "sources": [{"url": "https://example.test/reap"}],
                                },
                            }
                        ]
                    }
                ),
            )
        ]
        # A story is applied only if it cites a source from the scan's gathered evidence.
        ledger_row = {
            "custom_ids": [real_cid],
            "metadata": {"url_to_date": {"https://example.test/reap": "2026-06-11"}},
        }
        count = apply_intel_batch_results(fake_results, stored_handle, ledger_row)
        assert count == 1, f"expected 1 story applied, got {count}"

    finally:
        _seam._submit_batch = orig_submit
        _seam._collect_batch = orig_collect


# ---------------------------------------------------------------------------
# Blocker 2: _reap_expired_row BatchPending check is correct
# ---------------------------------------------------------------------------


def test_reap_expired_row_pending_not_applied(db_isolated):
    """When the sync fallback returns BatchPending, _reap_expired_row marks expired (not done)."""
    import asyncio

    from backend.core.llm_seam import BatchPending
    from backend.db.batch_ledger_store import BatchLedgerStore
    from backend.scheduler import _reap_expired_row

    store = BatchLedgerStore()
    store.add(
        "h-expired",
        "intel",
        "anthropic",
        "intel_scan",
        "m",
        ["intel-abc123"],
        metadata={"prompt": ""},  # empty prompt → no requests built → collect gets BatchPending
    )
    row = store.find_by_id("h-expired")

    async def _fake_submit(task, requests):
        return "fallback-handle"

    async def _fake_collect(handle):
        return BatchPending()

    from backend.core import llm_seam as _seam

    orig_submit = _seam._submit_batch
    orig_collect = _seam._collect_batch
    try:
        _seam._submit_batch = _fake_submit
        _seam._collect_batch = _fake_collect
        asyncio.run(_reap_expired_row(row, store))
    finally:
        _seam._submit_batch = orig_submit
        _seam._collect_batch = orig_collect

    updated = store.find_by_id("h-expired")
    assert updated["status"] == "expired", (
        f"expected 'expired' when collect returns BatchPending, got {updated['status']!r}"
    )


def test_reap_expired_row_results_applied(db_isolated, tmp_path):
    """When sync fallback returns results, _reap_expired_row marks done (not expired)."""
    import asyncio

    from backend.core.llm_seam import BatchResult

    # IntelStoriesStore is DB-backed now (plan 4d task 2) — db_isolated (already a
    # parameter above) redirects it to the same fresh, empty database as BatchLedgerStore;
    # no more per-store INTEL_SIGNALS_JSON to monkeypatch.
    from backend.db.batch_ledger_store import BatchLedgerStore
    from backend.scheduler import _reap_expired_row

    store = BatchLedgerStore()
    real_cid = "intel-cafebabe0000"
    store.add(
        "h-expired-ok",
        "intel",
        "anthropic",
        "intel_scan",
        "m",
        [real_cid],
        metadata={"prompt": "p"},
    )
    row = store.find_by_id("h-expired-ok")

    fake_result_text = json.dumps(
        {
            "stories": [
                {
                    "type": "asset",
                    "label": "Fallback Co",
                    "status": "monitoring",
                    "summary": "synced",
                    "new_timeline_event": {
                        "date": "2026-06-11",
                        "title": "sync",
                        "summary": "ok",
                        "sources": [],
                    },
                }
            ]
        }
    )

    async def _fake_submit(task, requests):
        return "fallback-sync-handle"

    async def _fake_collect(handle):
        return [BatchResult(custom_id=real_cid, text=fake_result_text)]

    from backend.core import llm_seam as _seam

    orig_submit = _seam._submit_batch
    orig_collect = _seam._collect_batch
    try:
        _seam._submit_batch = _fake_submit
        _seam._collect_batch = _fake_collect
        asyncio.run(_reap_expired_row(row, store))
    finally:
        _seam._submit_batch = orig_submit
        _seam._collect_batch = orig_collect

    updated = store.find_by_id("h-expired-ok")
    assert updated["status"] == "done", (
        f"expected 'done' when collect returns results, got {updated['status']!r}"
    )


# ---------------------------------------------------------------------------
# Blocker 3: collect_batch called exactly once per reap sweep row
# ---------------------------------------------------------------------------


def test_reap_collect_called_once_per_row(db_isolated, tmp_path):
    """The reap sweep collects exactly once per row and does NOT call collect again inside
    apply_intel_batch_results.  Prevents double cost-recording and one-shot stream failures."""
    import asyncio

    from backend.core.llm_seam import BatchResult

    real_cid = "intel-deadbeef0001"
    fake_result_text = json.dumps(
        {
            "stories": [
                {
                    "type": "asset",
                    "label": "Once Corp",
                    "status": "monitoring",
                    "summary": "once",
                    "new_timeline_event": {
                        "date": "2026-06-11",
                        "title": "once",
                        "summary": "ok",
                        "sources": [],
                    },
                }
            ]
        }
    )

    collect_call_count = 0

    async def _fake_collect(handle):
        nonlocal collect_call_count
        collect_call_count += 1
        if collect_call_count > 1:
            raise RuntimeError("collect_batch called more than once — double-collect bug")
        return [BatchResult(custom_id=real_cid, text=fake_result_text)]

    async def _fake_submit(task, requests):
        return "provider-batch-once"

    # IntelStoriesStore is DB-backed now (plan 4d task 2) — db_isolated (already a parameter
    # above) redirects it to the same fresh, empty database as BatchLedgerStore; no more
    # per-store INTEL_SIGNALS_JSON to monkeypatch.
    from backend.db.batch_ledger_store import BatchLedgerStore
    from backend.scheduler import _run_reap_batches

    store = BatchLedgerStore()
    store.add(
        "provider-batch-once",
        "intel",
        "anthropic",
        "intel_scan",
        "m",
        [real_cid],
        metadata={"url_to_date": {}},
    )

    from backend.core import llm_seam as _seam

    orig_submit = _seam._submit_batch
    orig_collect = _seam._collect_batch
    try:
        _seam._submit_batch = _fake_submit
        _seam._collect_batch = _fake_collect
        asyncio.run(_run_reap_batches())
    finally:
        _seam._submit_batch = orig_submit
        _seam._collect_batch = orig_collect

    assert collect_call_count == 1, f"collect_batch called {collect_call_count} time(s), want 1"
    # Verify the row was marked done (result was applied, not dropped)
    from backend.db.batch_ledger_store import BatchLedgerStore

    row = BatchLedgerStore().find_by_id("provider-batch-once")
    assert row["status"] == "done"


# ---------------------------------------------------------------------------
# Blocker 4: BATCH_LEDGER_JSON is excluded from checkpoints
# ---------------------------------------------------------------------------


def test_batch_ledger_checkpoint_excluded():
    """BATCH_LEDGER_JSON must appear in checkpoint._EXCLUDED_PATHS."""
    from backend.ai.checkpoint import _EXCLUDED_PATHS
    from backend.paths import BATCH_LEDGER_JSON

    assert BATCH_LEDGER_JSON.resolve() in _EXCLUDED_PATHS, (
        "BATCH_LEDGER_JSON is not excluded from checkpointing — every ledger write "
        "would fire a checkpoint snapshot (write amplification)"
    )


def test_batch_ledger_table_checkpoint_excluded():
    """DB mirror of the file-level pin above (plan 4c): batch_ledger is now DB-backed
    (``PayloadTableStore``) — ``ai.checkpoint._EXCLUDED_TABLES`` is the table-level
    counterpart to ``_EXCLUDED_PATHS``, which the test above still pins for the
    legacy (now cutover-absorbed) file."""
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "batch_ledger" in _EXCLUDED_TABLES, (
        "batch_ledger table is not checkpoint-excluded — every ledger write would "
        "fire a checkpoint snapshot (write amplification)"
    )


# ---------------------------------------------------------------------------
# Nit 7: openai_batch applies extra_params to request body
# ---------------------------------------------------------------------------


def test_openai_batch_extra_params_applied(tmp_path):
    """extra_params are merged into the OpenAI batch request body (not silently dropped)."""
    import asyncio
    from unittest.mock import MagicMock
    from unittest.mock import patch as mpatch

    from backend.core.llm_seam import BatchRequest

    captured_jsonl: list[str] = []

    class _FakeFile:
        id = "file-abc"

    class _FakeBatch:
        id = "batch-openai-1"

    class _FakeFiles:
        async def create(self, *, file, purpose):
            # Read and capture the JSONL content before it's deleted
            content = file.read()
            for line in content.decode().splitlines():
                if line.strip():
                    captured_jsonl.append(line)
            return _FakeFile()

    class _FakeBatches:
        async def create(self, *, input_file_id, endpoint, completion_window):
            return _FakeBatch()

    class _FakeClient:
        files = _FakeFiles()
        batches = _FakeBatches()

    fake_config = MagicMock()
    fake_config.resolved.return_value = {
        "provider": "openai_compatible",
        "model": "gpt-4o-mini",
        "api_key": "k",
        "base_url": "",
    }

    with (
        mpatch("backend.db.model_config_store.ModelConfigStore", lambda: fake_config),
        mpatch("openai.AsyncOpenAI", lambda **kw: _FakeClient()),
    ):
        from backend.ai.batch import openai_batch

        reqs = [
            BatchRequest(
                custom_id="oai-1",
                prompt="hello",
                system="sys",
                model="gpt-4o-mini",
                max_tokens=512,
                extra_params={"tools": [{"type": "function", "function": {"name": "do_thing"}}]},
            )
        ]
        asyncio.run(openai_batch.submit("test", reqs))

    assert captured_jsonl, "no JSONL lines captured from fake file upload"
    parsed = json.loads(captured_jsonl[0])
    body = parsed["body"]
    assert "tools" in body, f"extra_params['tools'] not merged into body; body keys: {list(body)}"
    assert body["tools"][0]["function"]["name"] == "do_thing"


# ---------------------------------------------------------------------------
# Nit 9: _sync_results dict is capped at _SYNC_RESULTS_CAP entries
# ---------------------------------------------------------------------------


def test_sync_results_cap_evicts_oldest():
    """Submitting more than _SYNC_RESULTS_CAP entries without collecting evicts oldest,
    preventing unbounded memory growth."""
    import asyncio

    from backend.core import llm_seam

    orig_submit = llm_seam._submit_batch
    orig_collect = llm_seam._collect_batch
    orig_complete = llm_seam._complete
    orig_results = dict(llm_seam._sync_results)

    async def _fake_complete(prompt, **_):
        return "ok"

    try:
        llm_seam._submit_batch = None
        llm_seam._collect_batch = None
        llm_seam._complete = _fake_complete
        llm_seam._sync_results.clear()

        cap = llm_seam._SYNC_RESULTS_CAP

        async def _fill():
            handles = []
            # Submit cap + 5 without collecting — should stay at cap, evicting oldest.
            for i in range(cap + 5):
                h = await llm_seam.submit_batch(
                    "test", [llm_seam.BatchRequest(custom_id=f"c{i}", prompt=f"p{i}")]
                )
                handles.append(h)
            return handles

        handles = asyncio.run(_fill())
        assert len(llm_seam._sync_results) == cap, (
            f"_sync_results grew to {len(llm_seam._sync_results)}, want {cap} (cap not enforced)"
        )
        # The first 5 handles should have been evicted; the last cap handles remain.
        for h in handles[:5]:
            assert h not in llm_seam._sync_results, f"oldest handle {h!r} was not evicted"
        for h in handles[-1:]:
            assert h in llm_seam._sync_results, f"newest handle {h!r} was unexpectedly evicted"

    finally:
        llm_seam._submit_batch = orig_submit
        llm_seam._collect_batch = orig_collect
        llm_seam._complete = orig_complete
        llm_seam._sync_results.clear()
        llm_seam._sync_results.update(orig_results)


def test_reap_expired_row_without_prompt_never_submits(db_isolated):
    """Regression: intel rows store no prompt in metadata — the expired-row path
    must mark them expired WITHOUT submitting a fake empty batch."""
    import asyncio

    from backend.core import llm_seam as _seam
    from backend.db.batch_ledger_store import BatchLedgerStore
    from backend.scheduler import _reap_expired_row

    store = BatchLedgerStore()
    store.add(
        "h-no-prompt",
        "intel",
        "anthropic",
        "intel_scan",
        "m",
        ["intel-abc123def456"],
        metadata={"url_to_date": {}},
    )
    row = store.find_by_id("h-no-prompt")

    calls = []

    async def _fake_submit(task, requests):
        calls.append((task, requests))
        return "should-never-happen"

    orig_submit = _seam._submit_batch
    try:
        _seam._submit_batch = _fake_submit
        asyncio.run(_reap_expired_row(row, store))
    finally:
        _seam._submit_batch = orig_submit

    assert calls == [], "no submit must happen for a promptless expired row"
    assert store.find_by_id("h-no-prompt")["status"] == "expired"


# ---------------------------------------------------------------------------
# I10 follow-up: BatchResult.stop_reason wiring per transport
# ---------------------------------------------------------------------------


def test_anthropic_batch_collect_surfaces_stop_reason():
    """anthropic_batch.collect() must populate BatchResult.stop_reason from the message's
    own stop_reason — this is the half of the I10 truncation-detection wiring that lives
    in the Anthropic transport (intel_agent's _parse_batch_result reads this field)."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from unittest.mock import patch as mpatch

    class _AsyncResultsIter:
        def __init__(self, items):
            self._items = items

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for item in self._items:
                yield item

    msg = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input={})],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        model="claude-haiku-4-5",
        stop_reason="max_tokens",
    )
    result_item = SimpleNamespace(
        custom_id="intel-anthropic-1",
        result=SimpleNamespace(type="succeeded", message=msg),
    )

    class _FakeBatches:
        async def retrieve(self, handle):
            return SimpleNamespace(id=handle, processing_status="ended")

        async def results(self, handle):
            return _AsyncResultsIter([result_item])

    class _FakeClient:
        messages = SimpleNamespace(batches=_FakeBatches())

    with (
        mpatch("anthropic.AsyncAnthropic", lambda **kw: _FakeClient()),
        mpatch("backend.db.provider_keys.effective_api_key", lambda provider: "fake-key"),
        mpatch("backend.ai.cost_tracker.record", MagicMock()),
    ):
        from backend.ai.batch import anthropic_batch

        results = asyncio.run(anthropic_batch.collect("batch-xyz"))

    assert len(results) == 1
    assert results[0].stop_reason == "max_tokens"
    assert results[0].text == "{}"


def test_openai_batch_collect_surfaces_stop_reason():
    """openai_batch.collect() must populate BatchResult.stop_reason from the choice's
    finish_reason — OpenAI's truncation vocabulary is "length", not Anthropic's
    "max_tokens", and openai_compatible is the DEFAULT provider so this path is reachable."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from unittest.mock import patch as mpatch

    raw_line = json.dumps(
        {
            "custom_id": "oai-trunc-1",
            "response": {
                "status_code": 200,
                "body": {
                    "model": "gpt-4o-mini",
                    "choices": [{"message": {"content": "{}"}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            },
        }
    )

    class _FakeBatches:
        async def retrieve(self, handle):
            return SimpleNamespace(id=handle, status="completed", output_file_id="file-out")

    class _FakeFiles:
        async def content(self, file_id):
            return SimpleNamespace(text=raw_line)

    class _FakeClient:
        batches = _FakeBatches()
        files = _FakeFiles()

    fake_config = MagicMock()
    fake_config.resolved.return_value = {
        "provider": "openai_compatible",
        "model": "gpt-4o-mini",
        "api_key": "k",
        "base_url": "",
    }

    with (
        mpatch("backend.db.model_config_store.ModelConfigStore", lambda: fake_config),
        mpatch("openai.AsyncOpenAI", lambda **kw: _FakeClient()),
        mpatch("backend.ai.cost_tracker.record", MagicMock()),
    ):
        from backend.ai.batch import openai_batch

        results = asyncio.run(openai_batch.collect("batch-openai-x"))

    assert len(results) == 1
    assert results[0].stop_reason == "length"
    assert results[0].text == "{}"
