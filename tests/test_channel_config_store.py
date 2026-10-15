"""ChannelConfigStore — the Webhook connector's row: CRUD + secret discipline.

The webhook is one ``connectors`` row (config ``secret_header_name``) with its ``url`` and
``secret`` in ``connector_secrets``; isolated per test by conftest's autouse Tier-2 table
clear; no network, no real secrets. Verifies:
  * defaults before the row exists (and that reading never creates it),
  * update creates the row and merges,
  * the URL and the secret live in ``connector_secrets`` — not in ``connectors``,
  * public() never leaks either (only has_* flags and the URL's host),
  * a blank url / secret on update keeps the saved one,
  * is_configured / is_active gates,
  * reset erases the row and its secrets,
  * nothing is seeded from the environment.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from backend.db.channel_config_store import ChannelConfigStore
from backend.db.connector_store import ConnectorStore
from backend.db.engine import get_engine
from backend.db.tables import connectors


@pytest.fixture
def store():
    return ChannelConfigStore()


def test_defaults_before_the_row_exists(store):
    cfg = store.get()
    assert cfg == {
        "enabled": False,
        "secret_header_name": "X-Webhook-Secret",
        "url": "",
        "secret": "",
    }
    assert ConnectorStore().get("webhook") is None


def test_update_creates_the_row_and_merges(store):
    store.update({"url": "https://x.test/hook", "secret_header_name": "X-Sig", "enabled": 1})
    cfg = store.get()
    assert cfg["url"] == "https://x.test/hook"
    assert cfg["secret_header_name"] == "X-Sig"
    assert cfg["enabled"] is True

    store.update({"enabled": False, "url": None, "unknown": "ignored"})
    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["url"] == "https://x.test/hook"


def test_url_and_secret_live_in_the_secrets_table_only(store):
    store.update({"url": "https://hooks.test/T0/B0/xyz", "secret": "hook-secret"})

    row = ConnectorStore().get("webhook")
    assert row["config"] == {}
    assert "xyz" not in json.dumps(row)
    assert "hook-secret" not in json.dumps(row)
    with get_engine().connect() as conn:
        raw = conn.execute(select(connectors)).mappings().all()
    assert "xyz" not in json.dumps([dict(r) for r in raw], default=str)
    assert ConnectorStore().secrets("webhook") == {
        "url": "https://hooks.test/T0/B0/xyz",
        "secret": "hook-secret",
    }


def test_public_never_leaks_url_or_secret(store):
    store.update({"secret": "hook-secret", "url": "https://hooks.test/T0/B0/xyz"})
    pub = store.public()
    assert "secret" not in pub
    assert "url" not in pub
    assert pub["has_secret"] is True
    assert pub["has_url"] is True
    assert pub["url_display"] == "hooks.test"
    assert "hook-secret" not in json.dumps(pub)
    assert "xyz" not in json.dumps(pub)


def test_blank_secret_and_url_keep_saved(store):
    store.update({"secret": "keep-hook", "url": "https://x.test/h"})
    store.update({"secret": "   ", "url": ""})  # whitespace / empty = blank
    assert store.get()["secret"] == "keep-hook"
    assert store.get()["url"] == "https://x.test/h"


def test_is_configured_and_active(store):
    assert store.is_configured() is False
    store.update({"url": "https://x.test/hook"})
    assert store.is_configured() is True
    assert store.is_active() is False  # configured but not enabled
    store.update({"enabled": True})
    assert store.is_active() is True


def test_public_all_lists_only_the_webhook(store):
    assert [c["name"] for c in store.public_all()] == ["webhook"]


def test_reset_erases_the_row_and_its_secrets(store):
    store.update({"url": "https://x.test/hook", "secret": "s", "enabled": True})
    store.reset()
    assert ConnectorStore().get("webhook") is None
    assert ConnectorStore().secrets("webhook") == {}
    assert store.get()["url"] == ""
    assert store.is_active() is False


def test_environment_never_seeds_the_webhook(monkeypatch):
    monkeypatch.setenv("CHANNEL_WEBHOOK_URL", "https://seeded.test/hook")
    monkeypatch.setenv("CHANNEL_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("CHANNEL_WEBHOOK_SECRET", "seed-secret")
    cfg = ChannelConfigStore().get()
    assert cfg["url"] == ""
    assert cfg["enabled"] is False
    assert cfg["secret"] == ""
