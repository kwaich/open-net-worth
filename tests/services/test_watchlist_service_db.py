"""services/watchlist.py's direct-import cleanup (plan 4b, task 3): read_watchlist /
write_watchlist / _watchlist_lock re-seated onto WatchlistStore's DB row instead of the raw
watchlist.json file.

LOCK DISCIPLINE is the load-bearing property here (see backend/db/watchlist_store.py's class
docstring CARE section): `_watchlist_lock` is now `_lock_for_table("watchlists")` — the SAME
lock instance WatchlistStore's own domain methods (create_watchlist/insert/...) take
internally — and `write_watchlist`'s underlying `_save_raw` is LOCK-FREE, so a caller doing
`with _watchlist_lock: wl = read_watchlist(); <mutate>; write_watchlist(wl)`
(price_monitor.py's/intel_agent.py's exact shape) must still serialize correctly against every
other lock holder. These tests prove that end to end, through the real production functions —
not a simulated file (contrast tests/test_custom_writer_concurrency.py::test_watchlist_no_lost_items,
which self-contains a raw-file simulation under the same lock object and needs no changes here).
"""

from __future__ import annotations

import threading


def _run_threads(fn, args_list: list) -> list[Exception]:
    """Run fn(arg) in one thread per arg; collect any exceptions. Mirrors
    tests/test_custom_writer_concurrency.py's helper."""
    errors: list[Exception] = []

    def wrapper(arg):
        try:
            fn(arg)
        except Exception as exc:  # noqa: BLE001 — surfaced via the returned list
            errors.append(exc)

    threads = [threading.Thread(target=wrapper, args=(a,)) for a in args_list]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


# ── read_watchlist / write_watchlist go through the DB (the brief's own example) ──


def test_read_write_watchlist_go_through_db(db_isolated):
    from backend.services.watchlist import _watchlist_lock, read_watchlist, write_watchlist

    with _watchlist_lock:
        wl = read_watchlist()
        wl["config"]["last_scan_at"] = "2026-08-08T00:00:00Z"
        write_watchlist(wl)
    assert read_watchlist()["config"]["last_scan_at"] == "2026-08-08T00:00:00Z"  # persisted to DB


def test_read_watchlist_seeds_default_on_first_call(db_isolated):
    """read_watchlist's underlying store._load() seeds my_holdings on a brand-new book,
    same as WatchlistStore.list_watchlists() does directly."""
    from backend.db.watchlist_store import MY_HOLDINGS_ID
    from backend.services.watchlist import read_watchlist

    wl = read_watchlist()
    assert any(w["id"] == MY_HOLDINGS_ID for w in wl["watchlists"])
    assert wl["items"] == []


def test_write_watchlist_folds_config_into_global_config_round_trip(db_isolated):
    """write_watchlist folds the legacy 'config' key back into global_config on persist;
    read_watchlist surfaces it back under 'config' — items pass through untouched."""
    from backend.services.watchlist import read_watchlist, write_watchlist

    wl = read_watchlist()
    wl["items"] = [{"id": "i1", "value": "AAPL", "type": "ticker", "watchlist_ids": ["ignored"]}]
    wl["config"]["search_budget"] = 42
    write_watchlist(wl)

    reread = read_watchlist()
    assert reread["config"]["search_budget"] == 42
    assert reread["items"] == wl["items"]

    from backend.db.watchlist_store import WatchlistStore

    raw = WatchlistStore()._load_raw()
    assert "config" not in raw  # folded into global_config, not stored verbatim
    assert raw["global_config"]["search_budget"] == 42


def test_write_watchlist_does_not_mutate_callers_dict(db_isolated):
    """write_watchlist builds on a COPY — callers (price_monitor, intel_agent) keep reading
    wl["config"] after the call returns; an in-place pop would leave them with a KeyError."""
    from backend.services.watchlist import read_watchlist, write_watchlist

    wl = read_watchlist()
    wl["config"]["last_price_check_at"] = "t0"
    write_watchlist(wl)
    assert wl["config"]["last_price_check_at"] == "t0"  # caller's dict still has "config"


# ── Compound read-modify-write under the lock stays atomic ──────────────────────


def test_compound_read_modify_write_under_lock_is_atomic(db_isolated):
    """N threads each perform the exact price_monitor/intel_agent compound shape —
    with _watchlist_lock: wl = read_watchlist(); <mutate>; write_watchlist(wl) — inserting
    ONE unique item apiece. Every item must survive: a lost update here means the table
    lock stopped serializing WatchlistStore's own writers against this external caller."""
    from backend.services.watchlist import _watchlist_lock, read_watchlist, write_watchlist

    n = 25

    def add_item(i: int) -> None:
        with _watchlist_lock:
            wl = read_watchlist()
            wl["items"].append(
                {
                    "id": f"wli-{i}",
                    "value": f"TICK{i}",
                    "type": "ticker",
                    "watchlist_ids": [wl["watchlists"][0]["id"]],
                }
            )
            write_watchlist(wl)

    errors = _run_threads(add_item, list(range(n)))
    assert not errors, f"worker errors: {errors}"

    final = read_watchlist()
    assert len(final["items"]) == n, (
        f"lost {n - len(final['items'])} watchlist items — expected {n}, got {len(final['items'])}"
    )
    assert {i["id"] for i in final["items"]} == {f"wli-{i}" for i in range(n)}


def test_price_monitor_style_rmw_sequence_is_serialized(db_isolated):
    """Mirrors backend/monitoring/price_monitor.py::run_price_check's exact shape verbatim:

        with _watchlist_lock:
            wl = read_watchlist()
            wl["config"]["last_price_check_at"] = ...
            write_watchlist(wl)

    N threads each run this heartbeat-stamp-plus-append sequence concurrently. The config
    stamp is last-write-wins by design (any final value is fine); the per-thread item append
    riding along in the SAME wl dict is the atomicity-sensitive part — it must never be lost
    to an interleaved read from another thread's in-flight write."""
    from datetime import datetime, timezone

    from backend.services.watchlist import _watchlist_lock, read_watchlist, write_watchlist

    n = 25
    tech_id_box: dict = {}

    def heartbeat(i: int) -> None:
        with _watchlist_lock:
            wl = read_watchlist()
            if "tech" not in tech_id_box:
                tech_id_box["tech"] = wl["watchlists"][0]["id"]
            wl["config"]["last_price_check_at"] = datetime.now(timezone.utc).isoformat()
            wl["items"].append(
                {
                    "id": f"pm-{i}",
                    "value": f"PM{i}",
                    "type": "ticker",
                    "watchlist_ids": [tech_id_box["tech"]],
                }
            )
            write_watchlist(wl)

    errors = _run_threads(heartbeat, list(range(n)))
    assert not errors, f"worker errors: {errors}"

    final = read_watchlist()
    assert final["config"]["last_price_check_at"]  # stamped (last writer wins, any value ok)
    assert len(final["items"]) == n, (
        f"lost {n - len(final['items'])} items during concurrent heartbeat writes"
    )
    assert {i["id"] for i in final["items"]} == {f"pm-{i}" for i in range(n)}
