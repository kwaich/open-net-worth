"""Durable Automation event queue contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.automation_store import AutomationStore
from backend.services.automations.events import (
    _matches,
    drain_automation_events,
    observe_balance_events,
    observe_price_events,
    publish_automation_events,
    replay_automation_event_deliveries,
)


@pytest.fixture
def isolated_automation_events(db_isolated):
    """AutomationStore + AutomationRuntimeStore are DB-backed (plan 4d task 2 / plan 4c
    task 1) -- db_isolated covers both."""


def _event_automation(*, limit: str = "each") -> dict:
    return AutomationStore().create(
        {
            "name": "Watch large debits",
            "instructions": "Review matching transactions.",
            "status": "active",
            "trigger": {
                "kind": "event",
                "event": "txn",
                "when": {"direction": "debit", "min_amount": 100},
                "limit": limit,
            },
            "execution": {"kind": "lucius"},
        }
    )


def test_publish_deduplicates_stable_source_event_keys(isolated_automation_events):
    automation = _event_automation()
    payload = {"event_key": "txn:a", "id": "a", "from_account_id": "acc", "amount": 125}

    assert publish_automation_events("txn", [payload, payload]) == 1
    assert publish_automation_events("txn", [payload]) == 0

    from backend.db.automation_runtime_store import AutomationRuntimeStore

    state = AutomationRuntimeStore().get_for_automation(automation["id"])
    assert [item["event_key"] for item in state["event_queue"]] == ["txn:a"]


def test_transaction_events_default_to_create_and_suppress_the_owning_run(
    isolated_automation_events,
):
    automation = _event_automation()
    base = {"from_account_id": "acc", "amount": 125}

    assert (
        publish_automation_events("txn", [{**base, "event_key": "update", "operation": "update"}])
        == 0
    )
    assert (
        publish_automation_events("txn", [{**base, "event_key": "delete", "operation": "delete"}])
        == 0
    )
    assert (
        publish_automation_events(
            "txn",
            [
                {
                    **base,
                    "event_key": "self-create",
                    "operation": "create",
                    "source_automation_id": automation["id"],
                    "source_automation_run_id": "run_self",
                }
            ],
        )
        == 0
    )
    assert publish_automation_events("txn", [{**base, "event_key": "create"}]) == 1


def test_transaction_direction_amount_and_currency_are_relative_to_selected_account():
    transfer = {
        "operation": "create",
        "from_account_id": "acc_sgd",
        "from_amount": -120,
        "from_currency": "SGD",
        "to_account_id": "acc_eur",
        "to_amount": 80,
        "to_currency": "EUR",
    }

    assert _matches(
        "txn",
        {
            "account": "acc_eur",
            "direction": "in",
            "currency": "EUR",
            "min_amount": 75,
            "max_amount": 85,
        },
        transfer,
    )
    assert _matches(
        "txn",
        {
            "account": "acc_sgd",
            "direction": "out",
            "currency": "SGD",
            "min_amount": 115,
            "max_amount": 125,
        },
        transfer,
    )
    assert not _matches(
        "txn",
        {"account": "acc_eur", "direction": "out", "currency": "SGD"},
        transfer,
    )


def test_failed_queue_write_is_replayed_from_the_durable_outbox(
    isolated_automation_events, monkeypatch
):
    _event_automation()
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    original = AutomationRuntimeStore.enqueue_events

    def fail_queue_write(self, automation_id, events, **kwargs):
        raise OSError("forced queue failure")

    monkeypatch.setattr(AutomationRuntimeStore, "enqueue_events", fail_queue_write)
    payload = {"event_key": "txn:replay", "from_account_id": "acc", "amount": 125}
    assert publish_automation_events("txn", [payload]) == 0
    assert AutomationRuntimeStore().pending_event_deliveries()

    monkeypatch.setattr(AutomationRuntimeStore, "enqueue_events", original)
    assert replay_automation_event_deliveries() == 1
    assert AutomationRuntimeStore().pending_event_deliveries() == []


def test_post_commit_transaction_event_staging_failure_never_reports_ledger_failure(
    isolated_automation_events, monkeypatch
):
    from backend.db.automation_runtime_store import AutomationRuntimeStore
    from backend.services.ledger import _publish_committed_transaction_events

    monkeypatch.setattr(
        AutomationRuntimeStore,
        "stage_event_delivery",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("outbox unavailable")),
    )

    _publish_committed_transaction_events(
        "create", [{"id": "txn_committed", "from_account_id": "acc", "amount": 125}]
    )


@pytest.mark.asyncio
async def test_hourly_batch_retains_later_events_until_next_window(
    isolated_automation_events, monkeypatch
):
    _event_automation(limit="hour")
    started = []

    async def fake_run(automation_id, *, trigger, payload, run_id=None):
        started.append(payload)
        return {"id": run_id or f"run_{len(started)}"}

    import backend.services.automations.events as events

    monkeypatch.setattr(events, "run_automation", fake_run)
    now = datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc)
    publish_automation_events(
        "txn",
        [
            {"event_key": "txn:a", "from_account_id": "acc", "amount": 125},
            {"event_key": "txn:b", "from_account_id": "acc", "amount": 150},
        ],
    )
    first = await drain_automation_events(now)
    publish_automation_events(
        "txn", [{"event_key": "txn:c", "from_account_id": "acc", "amount": 175}]
    )

    assert first[0]["event_ids"] == ["txn:a", "txn:b"]
    assert await drain_automation_events(now + timedelta(minutes=59)) == []
    later = await drain_automation_events(now + timedelta(hours=1))
    assert later[0]["event_ids"] == ["txn:c"]


def test_a_leftover_sync_delivery_is_dropped_not_retried_forever(isolated_automation_events):
    """The "connector sync fails" trigger was removed. A sync delivery staged before the
    upgrade must be acknowledged on replay, not raise and stay pending on every tick."""
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    store = AutomationRuntimeStore()
    store.stage_event_delivery("sync", {"connector": "bank", "status": "connect_error"})
    assert replay_automation_event_deliveries() == 0
    assert store.pending_event_deliveries() == []


def test_percentage_price_move_has_one_rising_edge_per_day(isolated_automation_events):
    AutomationStore().create(
        {
            "name": "Watch AAPL move",
            "instructions": "Tell me when AAPL moves five percent.",
            "status": "active",
            "trigger": {
                "kind": "event",
                "event": "price",
                "when": {"symbol": "AAPL", "direction": "move", "value": 5},
                "limit": "each",
            },
            "execution": {"kind": "lucius"},
        }
    )

    first = {"symbol": "AAPL", "price": 106, "previous_close": 100, "day": "2026-08-02"}
    next_day = {"symbol": "AAPL", "price": 112, "previous_close": 105, "day": "2026-08-03"}
    assert observe_price_events([first]) == 1
    assert observe_price_events([first]) == 0
    assert observe_price_events([next_day]) == 1


def _price_automation(direction: str, value: float) -> dict:
    return AutomationStore().create(
        {
            "name": f"Watch AAPL {direction}",
            "instructions": "Tell me.",
            "status": "active",
            "trigger": {
                "kind": "event",
                "event": "price",
                "when": {"symbol": "AAPL", "direction": direction, "value": value},
                "limit": "each",
            },
            "execution": {"kind": "lucius"},
        }
    )


def test_percentage_rise_fires_on_a_gain_and_never_on_a_drop(isolated_automation_events):
    _price_automation("rise", 5)
    drop = {"symbol": "AAPL", "price": 94, "previous_close": 100, "day": "2026-08-02"}
    gain = {"symbol": "AAPL", "price": 106, "previous_close": 100, "day": "2026-08-03"}
    assert observe_price_events([drop]) == 0
    assert observe_price_events([gain]) == 1
    assert observe_price_events([gain]) == 0


def test_percentage_fall_fires_on_a_drop_and_never_on_a_gain(isolated_automation_events):
    _price_automation("fall", 5)
    gain = {"symbol": "AAPL", "price": 106, "previous_close": 100, "day": "2026-08-02"}
    small_drop = {"symbol": "AAPL", "price": 97, "previous_close": 100, "day": "2026-08-03"}
    big_drop = {"symbol": "AAPL", "price": 94, "previous_close": 100, "day": "2026-08-03"}
    assert observe_price_events([gain]) == 0
    assert observe_price_events([small_drop]) == 0
    assert observe_price_events([big_drop]) == 1
    assert observe_price_events([big_drop]) == 0


def test_percentage_trigger_without_a_previous_close_is_skipped_not_read_as_a_price(
    isolated_automation_events,
):
    """No previous close = the move cannot be measured. It must not fall through to the
    price-level branch (price 106 > "5") and poison the day's armed state."""
    _price_automation("fall", 5)
    no_anchor = {"symbol": "AAPL", "price": 106, "day": "2026-08-02"}
    big_drop = {"symbol": "AAPL", "price": 94, "previous_close": 100, "day": "2026-08-02"}
    assert observe_price_events([no_anchor]) == 0
    assert observe_price_events([big_drop]) == 1


def test_price_threshold_rearms_after_crossing_back_on_the_same_day(
    isolated_automation_events,
):
    automation = AutomationStore().create(
        {
            "name": "Watch AAPL threshold",
            "instructions": "Tell me each time AAPL crosses above 100.",
            "status": "active",
            "trigger": {
                "kind": "event",
                "event": "price",
                "when": {"symbol": "AAPL", "direction": "above", "value": 100},
                "limit": "each",
            },
            "execution": {"kind": "lucius"},
        }
    )
    below = {"symbol": "AAPL", "price": 90, "day": "2026-08-02"}
    above = {"symbol": "AAPL", "price": 110, "day": "2026-08-02"}

    assert observe_price_events([below]) == 0
    assert observe_price_events([above]) == 1
    assert observe_price_events([above]) == 0
    assert observe_price_events([below]) == 0
    assert observe_price_events([above]) == 1

    from backend.db.automation_runtime_store import AutomationRuntimeStore

    state = AutomationRuntimeStore().get_for_automation(automation["id"])
    assert [item["event_key"] for item in state["event_queue"]] == [
        "price:AAPL:above:100.0:1",
        "price:AAPL:above:100.0:2",
    ]


def test_commit_balance_snapshot_catches_crossing_before_first_scheduler_poll(
    isolated_automation_events, monkeypatch
):
    AutomationStore().create(
        {
            "name": "Watch checking floor",
            "instructions": "Tell me when checking drops below 100.",
            "status": "active",
            "trigger": {
                "kind": "event",
                "event": "balance",
                "when": {"account": "acc", "direction": "below", "amount": 100},
                "limit": "each",
            },
            "execution": {"kind": "lucius"},
        }
    )
    import backend.services.ledger as ledger

    monkeypatch.setattr(ledger, "balances", lambda include_archived=True: {"acc": 90.0})

    assert observe_balance_events(before={"acc": 110.0}) == 1


def test_balance_threshold_rearms_after_crossing_back(isolated_automation_events):
    automation = AutomationStore().create(
        {
            "name": "Watch checking floor",
            "instructions": "Tell me each time checking drops below 100.",
            "status": "active",
            "trigger": {
                "kind": "event",
                "event": "balance",
                "when": {"account": "acc", "direction": "below", "amount": 100},
                "limit": "each",
            },
            "execution": {"kind": "lucius"},
        }
    )

    assert observe_balance_events(before={"acc": 110}, current={"acc": 90}) == 1
    assert observe_balance_events(before={"acc": 90}, current={"acc": 110}) == 0
    assert observe_balance_events(before={"acc": 110}, current={"acc": 90}) == 1

    from backend.db.automation_runtime_store import AutomationRuntimeStore

    state = AutomationRuntimeStore().get_for_automation(automation["id"])
    assert [item["event_key"] for item in state["event_queue"]] == [
        "balance:acc:below:100.0:1",
        "balance:acc:below:100.0:2",
    ]


@pytest.mark.asyncio
async def test_started_event_run_is_not_started_again_if_queue_ack_crashes(
    isolated_automation_events, monkeypatch
):
    automation = _event_automation()
    payload = {"event_key": "txn:crash", "from_account_id": "acc", "amount": 125}
    assert publish_automation_events("txn", [payload]) == 1

    from backend.db.automation_run_store import AutomationRunStore
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    started: list[str] = []

    async def fake_run(automation_id, *, trigger, payload, run_id=None):
        assert run_id
        started.append(run_id)
        return AutomationRunStore().start(
            {
                "id": run_id,
                "automation_id": automation_id,
                "session_id": f"automation-run-{run_id}",
                "trigger": trigger,
                "execution": {"kind": "lucius"},
                "context": payload,
            }
        )

    import backend.services.automations.events as events

    monkeypatch.setattr(events, "run_automation", fake_run)
    original_complete = AutomationRuntimeStore.complete_event_batch

    def crash_after_start(self, automation_id, run_id):
        raise SystemExit("simulated process crash after run start")

    monkeypatch.setattr(AutomationRuntimeStore, "complete_event_batch", crash_after_start)
    with pytest.raises(SystemExit, match="simulated process crash"):
        await drain_automation_events(datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc))

    state = AutomationRuntimeStore().get_for_automation(automation["id"])
    assert state["event_queue"] == []
    assert state["event_inflight"]["run_id"] == started[0]

    monkeypatch.setattr(AutomationRuntimeStore, "complete_event_batch", original_complete)
    assert await drain_automation_events(datetime(2026, 8, 2, 1, 1, tzinfo=timezone.utc)) == []
    assert len(started) == 1
    state = AutomationRuntimeStore().get_for_automation(automation["id"])
    assert "event_inflight" not in state
    assert state["event_queue"] == []


@pytest.mark.asyncio
async def test_stale_queued_events_cannot_run_after_trigger_config_was_persisted(
    isolated_automation_events, monkeypatch
):
    automation = _event_automation()
    assert (
        publish_automation_events(
            "txn",
            [{"event_key": "txn:old", "from_account_id": "acc", "amount": 125}],
        )
        == 1
    )
    AutomationStore().update_by_id(
        automation["id"],
        {
            "trigger_revision": "triggerrev_new",
            "trigger": {
                "kind": "event",
                "event": "price",
                "when": {"symbol": "AAPL", "direction": "below", "value": 100},
                "limit": "each",
            },
        },
    )
    started = []

    async def fake_run(*args, **kwargs):
        started.append((args, kwargs))
        return {"id": "should-not-run"}

    import backend.services.automations.events as events

    monkeypatch.setattr(events, "run_automation", fake_run)

    assert await drain_automation_events(datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc)) == []
    assert started == []

    from backend.db.automation_runtime_store import AutomationRuntimeStore

    state = AutomationRuntimeStore().get_for_automation(automation["id"])
    assert state.get("event_queue") in (None, [])


@pytest.mark.asyncio
async def test_stale_observer_cannot_reset_revision_or_discard_the_new_queue(
    isolated_automation_events, monkeypatch
):
    import backend.services.automations.events as events
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    stale = _event_automation()
    current = AutomationStore().update_by_id(
        stale["id"],
        {
            "trigger_revision": "triggerrev_new",
            "trigger": {
                "kind": "event",
                "event": "txn",
                "when": {"direction": "debit", "min_amount": 50},
                "limit": "each",
            },
        },
    )
    assert current is not None
    assert (
        publish_automation_events(
            "txn",
            [{"event_key": "txn:new", "from_account_id": "acc", "amount": 125}],
        )
        == 1
    )

    assert (
        events._enqueue(
            stale,
            "txn",
            [{"event_key": "txn:old", "from_account_id": "acc", "amount": 125}],
        )
        == 0
    )
    state = AutomationRuntimeStore().get_for_automation(stale["id"])
    assert state["trigger_revision"] == "triggerrev_new"
    assert [item["event_key"] for item in state["event_queue"]] == ["txn:new"]

    async def fake_run(automation_id, *, trigger, payload, run_id):
        return {"id": run_id, "automation_id": automation_id}

    monkeypatch.setattr(events, "run_automation", fake_run)
    [result] = await drain_automation_events(datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc))
    assert result["event_ids"] == ["txn:new"]


def test_failed_balance_queue_write_replays_crossing_without_consuming_edge(
    isolated_automation_events, monkeypatch
):
    automation = AutomationStore().create(
        {
            "name": "Watch checking floor",
            "instructions": "Tell me when checking drops below 100.",
            "status": "active",
            "trigger": {
                "kind": "event",
                "event": "balance",
                "when": {"account": "acc", "direction": "below", "amount": 100},
                "limit": "each",
            },
            "execution": {"kind": "lucius"},
        }
    )
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    original = AutomationRuntimeStore.enqueue_events

    def fail_queue_write(self, automation_id, events, **kwargs):
        if automation_id == automation["id"]:
            raise OSError("forced balance queue failure")
        return original(self, automation_id, events, **kwargs)

    monkeypatch.setattr(AutomationRuntimeStore, "enqueue_events", fail_queue_write)
    with pytest.raises(OSError, match="forced balance queue failure"):
        observe_balance_events(before={"acc": 110.0}, current={"acc": 90.0})
    assert AutomationRuntimeStore().pending_event_deliveries()

    monkeypatch.setattr(AutomationRuntimeStore, "enqueue_events", original)
    assert replay_automation_event_deliveries() == 1
    state = AutomationRuntimeStore().get_for_automation(automation["id"])
    assert [item["event"] for item in state["event_queue"]] == ["balance"]
    assert state["balance_observations"]["acc"] == 90.0
    assert AutomationRuntimeStore().pending_event_deliveries() == []


def test_balance_outbox_replay_does_not_duplicate_a_committed_crossing(
    isolated_automation_events, monkeypatch
):
    automation = AutomationStore().create(
        {
            "name": "Watch checking floor",
            "instructions": "Tell me each time checking drops below 100.",
            "status": "active",
            "trigger": {
                "kind": "event",
                "event": "balance",
                "when": {"account": "acc", "direction": "below", "amount": 100},
                "limit": "each",
            },
            "execution": {"kind": "lucius"},
        }
    )
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    original_ack = AutomationRuntimeStore.acknowledge_event_delivery

    def fail_ack(self, delivery_id):
        raise OSError("forced outbox ack failure")

    monkeypatch.setattr(AutomationRuntimeStore, "acknowledge_event_delivery", fail_ack)
    with pytest.raises(OSError, match="forced outbox ack failure"):
        observe_balance_events(before={"acc": 110.0}, current={"acc": 90.0})

    monkeypatch.setattr(AutomationRuntimeStore, "acknowledge_event_delivery", original_ack)
    assert replay_automation_event_deliveries() == 0
    state = AutomationRuntimeStore().get_for_automation(automation["id"])
    assert [item["event_key"] for item in state["event_queue"]] == ["balance:acc:below:100.0:1"]
    assert AutomationRuntimeStore().pending_event_deliveries() == []
