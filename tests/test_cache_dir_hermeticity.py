"""Regression guard: every cache file honors CACHE_DIR.

QA flagged that the price-monitor cache (``monitoring_price_cache.json``) and the
name/sector caches (``names_cache.json`` / ``sectors_cache.json``) once wrote to
the repo's ``backend/cache/`` directory even when ``CACHE_DIR`` was overridden —
i.e. their paths were hardcoded and ignored the env var, breaking the "set
CACHE_DIR and ALL caches go there" contract and test/disposable-instance
hermeticity. ``fx_cache.json`` / ``briefing_cache.json`` always resolved correctly
under ``CACHE_DIR``; these caches must too.

Two checks, mirroring the fx-cache isolation in ``test_fx_pivot.py`` and the
data-isolation backstop in ``test_data_isolation_guard.py``:

1. the path *constants* are children of the configured ``CACHE_DIR`` (proves they
   are routed through it, not joined onto a hardcoded ``backend/cache``);
2. the actual writers persist under the configured cache dir and never touch the
   repo's ``backend/cache/`` directory.
"""

from __future__ import annotations

import json

import backend.core.config as config
import backend.monitoring.price_monitor as price_monitor
import backend.paths as paths
import backend.services.prices as prices

# The repo's historical cache dir — the place these files must NOT land in once
# CACHE_DIR is overridden. Resolved the same way core.config derives its default.
_REPO_CACHE = config._DEFAULT_CACHE_DIR


def test_cache_constants_resolve_under_cache_dir():
    """All cache-file constants live under the configured CACHE_DIR, not the repo."""
    cache_dir = config.get_settings().cache_dir
    assert str(cache_dir) == str(paths.CACHE_DIR)
    for const in (
        paths.FX_CACHE_JSON,  # the caches that already worked — the contract baseline
        paths.BRIEFING_CACHE_JSON,
        paths.MONITORING_PRICE_CACHE_JSON,  # the QA-flagged offenders
        paths.NAMES_CACHE_JSON,
        paths.SECTORS_CACHE_JSON,
    ):
        assert const.parent == cache_dir, f"{const} is not under CACHE_DIR {cache_dir}"
    # And the throwaway test cache dir is never the repo's backend/cache.
    assert cache_dir != _REPO_CACHE


def test_monitoring_price_cache_writes_under_cache_dir(monkeypatch, tmp_path):
    """price_monitor._save_price_cache lands in CACHE_DIR, not the repo cache."""
    cache_path = tmp_path / "monitoring_price_cache.json"
    monkeypatch.setattr(price_monitor, "PRICE_CACHE_FILE", cache_path)

    payload = {"AAPL": {"price": 199.0, "alert_states": {}}}
    price_monitor._save_price_cache(payload)

    assert cache_path.exists()
    assert json.loads(cache_path.read_text()) == payload
    assert (
        not (_REPO_CACHE / "monitoring_price_cache.json").exists()
        or (_REPO_CACHE / "monitoring_price_cache.json").resolve() != cache_path.resolve()
    )
    # Round-trips back through the loader (incl. the float-migration path).
    assert price_monitor._load_price_cache() == payload


def test_name_and_sector_caches_write_under_cache_dir(monkeypatch, tmp_path):
    """save_names_cache / save_sectors_cache land in CACHE_DIR, not the repo cache."""
    names_path = tmp_path / "names_cache.json"
    sectors_path = tmp_path / "sectors_cache.json"
    monkeypatch.setattr(prices, "_NAMES_CACHE_FILE", names_path)
    monkeypatch.setattr(prices, "_SECTORS_CACHE_FILE", sectors_path)

    prices.save_names_cache({"AAPL": "Apple Inc."})
    prices.save_sectors_cache({"AAPL": "Technology"})

    assert json.loads(names_path.read_text()) == {"AAPL": "Apple Inc."}
    assert json.loads(sectors_path.read_text()) == {"AAPL": "Technology"}
    assert prices.load_names_cache() == {"AAPL": "Apple Inc."}
    assert prices.load_sectors_cache() == {"AAPL": "Technology"}
