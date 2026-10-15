"""AI tool-dispatch tests for the parity-backfill tools (docs/parity-audit.md).

Each new/extended @tool gets a behavioral test: drive it via execute_tool and assert
it performs the action / returns the right shape. Stores are isolated into tmp_path by
monkeypatching their backing-path constants; execute_tool constructs the store per call
so the patch takes effect. Offline — no LLM, no network.

Run:
    .venv/bin/python3 -m pytest tests/test_parity_backfill_tools.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


# ── registry: the new tools exist ──────────────────────────────────────────────


def test_new_parity_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {
        "scan_intel",
        "dismiss_intel_signals",
        "update_commitments",
        "dismiss_intel_signals",
        "dismiss_base_currency_review",
    } <= names


# ── intel: dismiss a story-arc signal (batch-pluralized 2026-07-19: dismiss_intel_signal
# -> dismiss_intel_signals; the former singular name is a registry alias). Param-arity
# follow-up (2026-07-19d): the batch param itself renamed signal_id -> signal_ids (the old
# singular key still resolves — read-side fallback, no alias table needed for a param
# rename). ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_intel(db_isolated):
    # IntelStoriesStore is DB-backed now (plan 4d task 2) — db_isolated redirects it.
    return db_isolated


def test_dismiss_intel_signal_deletes(isolated_intel):
    """Also exercises the pre-2026-07-19d singular ``signal_id`` key — must keep
    resolving as a read-side fallback."""
    from backend.db.intel_stories_store import IntelStoriesStore

    store = IntelStoriesStore()
    store.upsert({"id": "sig1", "type": "asset", "label": "Story", "status": "active"})
    assert store.find_by_id("sig1") is not None

    res, proposed = call("dismiss_intel_signals", signal_id="sig1")
    assert res["ok"] is True and res["dismissed"] == "sig1" and proposed is None
    assert store.find_by_id("sig1") is None


def test_dismiss_intel_signals_deletes_via_plural_key(isolated_intel):
    """The current schema key (2026-07-19d param-arity fixup): ``signal_ids``."""
    from backend.db.intel_stories_store import IntelStoriesStore

    store = IntelStoriesStore()
    store.upsert({"id": "sig1", "type": "asset", "label": "Story", "status": "active"})
    assert store.find_by_id("sig1") is not None

    res, proposed = call("dismiss_intel_signals", signal_ids="sig1")
    assert res["ok"] is True and res["dismissed"] == "sig1" and proposed is None
    assert store.find_by_id("sig1") is None


def test_dismiss_intel_signal_not_found(isolated_intel):
    res, _ = call("dismiss_intel_signals", signal_id="ghost")
    assert res["ok"] is False and "not found" in res["error"]


def test_dismiss_intel_signal_alias_resolves(isolated_intel):
    """The pre-2026-07-19 singular name must still resolve (via registry alias)."""
    from backend.db.intel_stories_store import IntelStoriesStore

    store = IntelStoriesStore()
    store.upsert({"id": "sig1", "type": "asset", "label": "Story", "status": "active"})

    res, proposed = call("dismiss_intel_signals", signal_id="sig1")
    assert res["ok"] is True and res["dismissed"] == "sig1" and proposed is None
    assert store.find_by_id("sig1") is None


def test_dismiss_intel_signals_batch_reports_each_outcome(isolated_intel):
    from backend.db.intel_stories_store import IntelStoriesStore

    store = IntelStoriesStore()
    store.upsert({"id": "sig1", "type": "asset", "label": "Story A", "status": "active"})
    store.upsert({"id": "sig2", "type": "asset", "label": "Story B", "status": "active"})

    res, proposed = call("dismiss_intel_signals", signal_id=["sig1", "ghost", "sig2"])
    assert proposed is None
    assert res["ok"] is False  # one of the three failed
    assert res["dismissed_count"] == 2
    assert [r.get("ok", False) for r in res["results"]] == [True, False, True]
    assert store.find_by_id("sig1") is None
    assert store.find_by_id("sig2") is None


def test_dismiss_intel_signals_batch_via_plural_key(isolated_intel):
    from backend.db.intel_stories_store import IntelStoriesStore

    store = IntelStoriesStore()
    store.upsert({"id": "sig1", "type": "asset", "label": "Story A", "status": "active"})
    store.upsert({"id": "sig2", "type": "asset", "label": "Story B", "status": "active"})

    res, proposed = call("dismiss_intel_signals", signal_ids=["sig1", "ghost", "sig2"])
    assert proposed is None
    assert res["ok"] is False  # one of the three failed
    assert res["dismissed_count"] == 2
    assert [r.get("ok", False) for r in res["results"]] == [True, False, True]
    assert store.find_by_id("sig1") is None
    assert store.find_by_id("sig2") is None


# ── email comms blocks: email_search_messages/email_get_messages + email_send_message ──


@pytest.fixture
def isolated_email(tmp_path, monkeypatch):
    # EmailConfigStore is DB-backed now (Wave 5) — conftest's autouse Tier-2 table clear
    # already isolates it per test, no __init__ override needed.
    from backend.db.email_config_store import EmailConfigStore

    EmailConfigStore().update(
        {
            "imap_host": "imap.example.com",
            "address": "me@example.com",
            "password": "app-pw",
            "enabled": True,
        }
    )
    return tmp_path


def test_email_search_messages_not_configured(tmp_path, monkeypatch):
    # The Email connector is a database row; conftest's autouse Tier-2 table clear isolates it.
    # A window is required, and argument validation runs BEFORE the credentials check — so
    # pass one, otherwise this asserts on the wrong refusal.
    res, _ = call("email_search_messages", since="2000-01-01")
    assert res["ok"] is False and "not configured" in res["error"].lower()


def test_email_send_message_uses_smtp(monkeypatch):
    """email_send_message composes + sends via the email connector's SMTP send path; it never
    returns a secret and confirms the recipient/subject/body it was given."""
    import backend.connectors.email_out.channel as chan

    captured: dict = {}

    def fake_send(to, subject, body, cc=None):
        captured.update({"to": to, "subject": subject, "body": body, "cc": cc})
        return {"ok": True, "sent": 1, "to": to}

    monkeypatch.setattr(chan, "send_email", fake_send)

    res, proposed = call(
        "email_send_message",
        to="boss@x.com",
        subject="Update",
        body="Net worth is up.",
        cc="cc@x.com",
    )
    assert proposed is None
    assert res["ok"] is True and res["sent"] == 1
    assert captured == {
        "to": "boss@x.com",
        "subject": "Update",
        "body": "Net worth is up.",
        "cc": "cc@x.com",
    }


# ── accounts: asset_class net-worth band on create + update ─────────────────────


@pytest.fixture
def isolated_accounts(tmp_path, monkeypatch, db_isolated):
    # AccountStore is DB-backed now (plan 3 task 4) — db_isolated redirects the
    # module-level ledger_service.accounts singleton (and every other unpinned store)
    # to a fresh, empty database.
    # base_currency() reads core.fx → profile; pin it so create() is offline.
    import backend.core.fx as fx

    monkeypatch.setattr(fx, "base_currency", lambda: "SGD")
    return tmp_path


def test_add_account_with_asset_class_is_refused(isolated_accounts):
    """`asset_class` retired with the seven-type collapse (ADR 2026-09-07-seven-account-
    types-one-axis) — a property is now an OWNED ASSET, never an account band override.
    services.accounts.assert_no_dead_account_fields refuses it outright."""
    res, proposed = call(
        "create_account",
        name="Apartment equity",
        type="cash",
        asset_class="property",
    )
    assert res["ok"] is False and proposed is None
    assert "asset_class" in res["error"]


def test_update_account_with_asset_class_is_refused(isolated_accounts):
    from backend.services import ledger as svc

    acct = svc.accounts.create({"name": "Brokerage", "type": "investment"})
    aid = acct["id"]

    res, _ = call("update_accounts", account_id=aid, asset_class="equity")
    assert res["ok"] is False and "asset_class" in res["error"]


# ── profile: briefing_preferences deep-merge + threshold + review-flag clear ────


@pytest.fixture
def isolated_profile(tmp_path, monkeypatch):
    # profile is DB-backed now (Wave 5, JSON->DB) — conftest's autouse fixture already
    # resets the shared row to {} before every test, no monkeypatch needed.
    return tmp_path


def test_update_profile_briefing_prefs_deep_merge(isolated_profile):
    # Seed a profile with two section flags on.
    call(
        "update_profile",
        changes={"briefing_preferences": {"fx": True, "spending": True}},
    )
    # A partial update of one flag must NOT clobber the other.
    res, _ = call(
        "update_profile",
        changes={"briefing_preferences": {"spending": False, "custom_focus": "macro"}},
    )
    prefs = res["profile"]["briefing_preferences"]
    assert prefs["fx"] is True  # preserved
    assert prefs["spending"] is False  # updated
    assert prefs["custom_focus"] == "macro"  # new


def test_update_profile_cashflow_threshold_coerced(isolated_profile):
    res, _ = call(
        "update_profile",
        changes={"cashflow_low_balance_threshold": "2500"},
    )
    assert res["profile"]["cashflow_low_balance_threshold"] == 2500.0


def test_dismiss_base_currency_review_clears_flag(isolated_profile):
    import backend.ai.tools as _root

    profile = _root.read_profile()
    profile["base_currency_review"] = {"from": "SGD", "to": "USD", "fields": []}
    _root.write_profile(profile)

    res, proposed = call("dismiss_base_currency_review")
    assert res["ok"] is True and res["cleared"] is True and proposed is None
    assert "base_currency_review" not in _root.read_profile()


# ── commitments: update top-level fields (status / rename / re-link) ────────────


@pytest.fixture
def isolated_commitments(tmp_path, db_isolated):
    # CommitmentsStore is DB-backed now (plan 4a task 3) — db_isolated covers it.
    return tmp_path


def test_update_commitment_top_level_fields(isolated_commitments):
    res, _ = call("create_commitment", name="Reno", currency="sgd", kind="purchase")
    cid = res["commitment"]["id"]

    upd, proposed = call(
        "update_commitments",
        commitment_id=cid,
        name="Renovation 2026",
        status="completed",
        note="done",
    )
    assert upd["ok"] is True and proposed is None
    assert upd["commitment"]["name"] == "Renovation 2026"
    assert upd["commitment"]["status"] == "completed"
    assert upd["commitment"]["note"] == "done"


def test_update_commitment_not_found(isolated_commitments):
    res, _ = call("update_commitments", commitment_id="ghost", name="x")
    assert res["ok"] is False and "not found" in res["error"]


# ── watchlist: search_budget cost-control knob ──────────────────────────────────


@pytest.fixture
def isolated_watchlist(tmp_path, monkeypatch, db_isolated):
    return tmp_path


def test_update_monitoring_config_sets_search_budget(isolated_watchlist):
    from backend.db import WatchlistStore

    res, proposed = call("update_monitoring_config", search_budget=30)
    assert res["ok"] is True and proposed is None
    assert WatchlistStore().get_config()["search_budget"] == 30
