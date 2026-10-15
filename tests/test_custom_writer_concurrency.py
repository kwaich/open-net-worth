"""Regression tests: custom (non-JsonStore) writers must not lose updates under
concurrent access.

Before the fixes, four custom writers had unlocked read-modify-write cycles:
  1. cost_tracker.record()        — cost_log.json
  2. checkpoint._append_index()   — checkpoints/index.json
  3. watchlist write_watchlist()  — watchlist.json
  4. fx_cache.save_fx_cache()     — fx_cache.json
  5. profile write_profile_raw()  — profile.json

For each we spawn N threads doing simultaneous writes and assert that ALL writes
survive (no lost update). We mirror the pattern from test_json_store_concurrency.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

N = 40  # threads; enough to expose races without being slow


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _run_threads(fn, args_list: list) -> list[Exception]:
    """Run fn(arg) in N threads; collect any exceptions."""
    errors: list[Exception] = []

    def wrapper(arg):
        try:
            fn(arg)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=wrapper, args=(a,)) for a in args_list]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


# ─────────────────────────────────────────────────────────────────────────────
# 1. cost_tracker — record() appends to a list; no entry must be lost
# ─────────────────────────────────────────────────────────────────────────────


def test_cost_tracker_no_lost_records(tmp_path: Path) -> None:
    """N threads each append one record; all N must survive in the JSON file."""
    from backend.ai.cost_tracker import _COST_LOG_MAX, _record_lock
    from backend.core.io import atomic_write_text

    log_path = tmp_path / "cost_log.json"

    def append_one(i: int) -> None:
        with _record_lock:
            records: list[dict] = []
            if log_path.exists():
                try:
                    records = json.loads(log_path.read_text(encoding="utf-8"))
                except Exception:
                    records = []
            records.append({"i": i})
            if len(records) > _COST_LOG_MAX:
                records = records[-_COST_LOG_MAX:]
            atomic_write_text(log_path, json.dumps(records))

    errors = _run_threads(append_one, list(range(N)))
    assert not errors, f"worker errors: {errors}"

    records = json.loads(log_path.read_text())
    assert len(records) == N, (
        f"lost {N - len(records)} cost records — expected {N}, got {len(records)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. checkpoint index — _append_index appends entries; none must be lost
# ─────────────────────────────────────────────────────────────────────────────


def test_checkpoint_index_no_lost_entries(tmp_path: Path) -> None:
    """N concurrent _append_index calls, all serialised by _index_lock; all entries must survive."""
    import backend.ai.checkpoint as cp
    from backend.core.io import atomic_write_text

    index_path = tmp_path / "index.json"

    def append_entry(i: int) -> None:
        with cp._index_lock:
            index: list[dict] = []
            if index_path.exists():
                try:
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                except Exception:
                    index = []
            index.append({"id": f"cp-{i:04d}", "status": "active"})
            atomic_write_text(index_path, json.dumps(index, ensure_ascii=False))

    errors = _run_threads(append_entry, list(range(N)))
    assert not errors, f"worker errors: {errors}"

    index = json.loads(index_path.read_text())
    assert len(index) == N, (
        f"lost {N - len(index)} checkpoint entries — expected {N}, got {len(index)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. watchlist — concurrent locked read/write/write_watchlist; no item lost
# ─────────────────────────────────────────────────────────────────────────────


def test_watchlist_no_lost_items(tmp_path: Path) -> None:
    """N threads each add one item under _watchlist_lock; all N must survive."""
    import uuid

    from backend.api.watchlist import _watchlist_lock
    from backend.core.io import atomic_write_text

    wl_path = tmp_path / "watchlist.json"
    wl_path.write_text(json.dumps({"items": [], "config": {}}))

    def add_item(i: int) -> None:
        item_id = str(uuid.uuid4())
        with _watchlist_lock:
            wl = json.loads(wl_path.read_text())
            wl["items"].append({"id": item_id, "value": f"TICK{i}"})
            atomic_write_text(wl_path, json.dumps(wl, ensure_ascii=False))

    errors = _run_threads(add_item, list(range(N)))
    assert not errors, f"worker errors: {errors}"

    wl = json.loads(wl_path.read_text())
    assert len(wl["items"]) == N, (
        f"lost {N - len(wl['items'])} watchlist items — expected {N}, got {len(wl['items'])}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. fx_cache — concurrent save_fx_cache calls; each unique rate must survive
# ─────────────────────────────────────────────────────────────────────────────


def test_fx_cache_no_lost_rates(tmp_path: Path) -> None:
    """N threads each write a unique currency rate; all N must survive."""
    from datetime import datetime

    from backend.core.io import atomic_write_text
    from backend.services.fx_cache import _fx_cache_lock

    cache_path = tmp_path / "fx_cache.json"

    def save_rate(i: int) -> None:
        rate_key = f"CUR{i:03d}"
        with _fx_cache_lock:
            existing: dict = {}
            if cache_path.exists():
                try:
                    data = json.loads(cache_path.read_text())
                    existing = data.get("rates", {})
                except Exception:
                    pass
            existing[rate_key] = 1.0 + i * 0.01
            atomic_write_text(
                cache_path,
                json.dumps({"rates": existing, "updated": datetime.now().isoformat()}),
            )

    errors = _run_threads(save_rate, list(range(N)))
    assert not errors, f"worker errors: {errors}"

    assert cache_path.exists(), "fx_cache.json was never written"
    data = json.loads(cache_path.read_text())
    saved_keys = {k for k in data.get("rates", {}) if k.startswith("CUR")}
    assert len(saved_keys) == N, (
        f"lost {N - len(saved_keys)} FX rates — expected {N}, got {len(saved_keys)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. profile write_profile_raw — concurrent locked writes; no key lost
# ─────────────────────────────────────────────────────────────────────────────


def test_profile_write_no_lost_updates(db_isolated) -> None:
    """N threads each add a unique key via ProfileStore.set_key; all N must survive.

    Profile persistence moved to the DB-backed ProfileStore (Wave 5) — there is no more
    budgets_mod._profile_write_lock guarding a hand-rolled read-modify-write over
    profile.json. set_key()'s own docstring says it directly: "the exact race the old
    _profile_write_lock guarded, now enforced by the DB instead of a per-process lock"
    (one transaction per call, serialized by the store's own per-table lock). No
    external lock needed in the test either — that is exactly the invariant under test.
    """
    from backend.db.profile_store import ProfileStore

    store = ProfileStore()
    store.set_raw({"base_currency": "USD"})

    def add_key(i: int) -> None:
        key = f"test_key_{i:04d}"
        store.set_key(key, True)

    errors = _run_threads(add_key, list(range(N)))
    assert not errors, f"worker errors: {errors}"

    profile = store.get_raw()
    saved_keys = {k for k in profile if k.startswith("test_key_")}
    assert len(saved_keys) == N, (
        f"lost {N - len(saved_keys)} profile updates — expected {N}, got {len(saved_keys)}"
    )
