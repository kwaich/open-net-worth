"""AI-tool dispatch tests for the Alpaca connector (read-sync only).

Pins that ``alpaca_get_status`` — an alias onto the merged ``get_connector_status`` read —
scopes to Alpaca alone and NEVER echoes the key pair back to the model.

The three trading tools (``propose_alpaca_order`` / ``apply_alpaca_order`` /
``cancel_alpaca_order``) and their nonce store were REMOVED 2026-07-19 along with the rest of
the trading half — see ``docs/adr/2026-07-19-remove-alpaca-trading-half.md``. The tests that
pinned their propose/apply/nonce contract went with them.

Offline, isolated to tmp_path, no LLM / network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    import backend.paths as paths

    monkeypatch.setattr(paths, "ALPACA_CONFIG_JSON", tmp_path / "alpaca_config.json")
    for var in ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "ALPACA_ENV", "ALPACA_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    yield


def test_status_tool_hides_keys():
    from backend.db.alpaca_config_store import AlpacaConfigStore

    AlpacaConfigStore().update({"api_key_id": "PKSECRET", "secret_key": "topsecret"})
    # Alpaca's status is now a row in the merged get_connector_status read; `alpaca_get_status`
    # still dispatches (alias) and still scopes to Alpaca alone.
    wrapper, _ = call("alpaca_get_status")
    body = wrapper["providers"]["alpaca"]
    assert body["has_keys"] is True
    blob = json.dumps(body)
    assert "PKSECRET" not in blob
    assert "topsecret" not in blob


def test_trading_tools_are_gone_from_the_registry():
    """The removal is the contract: these three names must not resolve — not as tools, and
    not as aliases either (there is no rename here; the capability is gone)."""
    from backend.ai.tools.registry import registered_tool_names

    names = set(registered_tool_names())
    for gone in ("propose_alpaca_order", "apply_alpaca_order", "cancel_alpaca_order"):
        assert gone not in names
