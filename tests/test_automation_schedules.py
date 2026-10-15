from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.services.automations.schedules import (
    compute_next_run,
    describe_trigger,
    validate_trigger,
)


def test_manual_trigger_validates_without_a_next_run():
    trigger = {"kind": "manual"}

    assert validate_trigger(trigger) == trigger
    assert compute_next_run(trigger, datetime.fromisoformat("2026-08-07T09:00:00+08:00")) is None
    assert describe_trigger(trigger) == "Manual"


def test_weekdays_skips_the_weekend():
    trigger = {"kind": "schedule", "type": "weekdays", "time": "08:00", "tzid": "Asia/Singapore"}

    assert (
        compute_next_run(trigger, datetime.fromisoformat("2026-08-07T09:00:00+08:00"))
        == "2026-08-10T08:00:00+08:00"
    )


def test_timezone_less_schedule_uses_the_server_local_timezone(monkeypatch):
    import backend.services.automations.schedules as schedules

    monkeypatch.setattr(schedules, "_local_tz", lambda: ZoneInfo("America/New_York"), raising=False)
    trigger = {"kind": "schedule", "type": "daily", "time": "08:00"}

    assert compute_next_run(trigger, datetime.fromisoformat("2026-08-07T10:00:00+00:00")) == (
        "2026-08-07T08:00:00-04:00"
    )


@pytest.mark.parametrize(
    ("trigger", "after", "expected"),
    [
        (
            {"kind": "schedule", "type": "once", "at": "2026-08-08T09:00:00+08:00"},
            "2026-08-07T09:00:00+08:00",
            "2026-08-08T09:00:00+08:00",
        ),
        (
            {"kind": "schedule", "type": "daily", "time": "08:00", "tzid": "Asia/Singapore"},
            "2026-08-07T09:00:00+08:00",
            "2026-08-08T08:00:00+08:00",
        ),
        (
            {
                "kind": "schedule",
                "type": "weekly",
                "day": "mon",
                "time": "08:00",
                "tzid": "Asia/Singapore",
            },
            "2026-08-07T09:00:00+08:00",
            "2026-08-10T08:00:00+08:00",
        ),
        (
            {
                "kind": "schedule",
                "type": "monthly",
                "day": 31,
                "time": "08:00",
                "tzid": "Asia/Singapore",
            },
            "2026-04-01T09:00:00+08:00",
            "2026-04-30T08:00:00+08:00",
        ),
        (
            {"kind": "schedule", "type": "interval", "seconds": 60},
            "2026-08-07T09:00:00+08:00",
            "2026-08-07T09:01:00+08:00",
        ),
        (
            {
                "kind": "schedule",
                "type": "rrule",
                "rrule": "FREQ=WEEKLY;BYDAY=MO",
                "dtstart": "2026-08-03T08:00:00+08:00",
                "tzid": "Asia/Singapore",
            },
            "2026-08-07T09:00:00+08:00",
            "2026-08-10T08:00:00+08:00",
        ),
    ],
)
def test_schedule_types_compute_the_next_strictly_future_run(trigger, after, expected, monkeypatch):
    # The tz-less cases (once/interval) render in the SERVER's local timezone — pin it so
    # the +08:00 expectations hold on any machine (found failing on a +07:00 host: same
    # instant, different offset string).
    import backend.services.automations.schedules as schedules

    monkeypatch.setattr(schedules, "_local_tz", lambda: ZoneInfo("Asia/Singapore"))
    assert compute_next_run(trigger, datetime.fromisoformat(after)) == expected


@pytest.mark.parametrize(
    "trigger",
    [
        {"kind": "schedule", "type": "daily", "time": "25:00"},
        {"kind": "schedule", "type": "daily", "time": "08:00", "tzid": ""},
        {"kind": "schedule", "type": "daily", "time": "08:00", "tzid": None},
        {"kind": "schedule", "type": "daily", "time": "08:00", "tzid": "Mars/Olympus"},
        {"kind": "schedule", "type": "interval", "seconds": 59},
        {"kind": "schedule", "type": "rrule", "rrule": "RRULE:FREQ=DAILY"},
        {"kind": "schedule", "type": "weekdays", "time": "08:00", "surprise": True},
        {"kind": "event", "event": "txn", "when": {}, "limit": "each", "extra": True},
    ],
)
def test_trigger_validation_rejects_invalid_or_unknown_fields(trigger):
    with pytest.raises(ValueError):
        validate_trigger(trigger)


def test_event_trigger_validates_and_is_never_scheduled():
    trigger = {
        "kind": "event",
        "event": "price",
        "when": {"symbol": "AAPL", "direction": "above", "value": 200},
        "limit": "hour",
    }

    assert validate_trigger(trigger) == trigger
    assert compute_next_run(trigger, datetime.fromisoformat("2026-08-07T09:00:00+08:00")) is None
    assert describe_trigger(trigger) == "When AAPL price changes (hourly batch)"


@pytest.mark.parametrize(
    ("trigger", "expected_when"),
    [
        (
            {"kind": "event", "event": "txn", "when": {}, "limit": "hour"},
            {"operation": "create"},
        ),
        (
            {
                "kind": "event",
                "event": "balance",
                "when": {"account": "acc_cash", "direction": "below", "amount": "250.50"},
                "limit": "day",
            },
            {"account": "acc_cash", "direction": "below", "amount": 250.5},
        ),
        (
            {
                "kind": "event",
                "event": "price",
                "when": {"symbol": "AAPL", "direction": "move", "value": "5"},
                "limit": "each",
            },
            {"symbol": "AAPL", "direction": "move", "value": 5.0},
        ),
        (
            {
                "kind": "event",
                "event": "price",
                "when": {"symbol": "AAPL", "direction": "rise", "value": 3},
                "limit": "day",
            },
            {"symbol": "AAPL", "direction": "rise", "value": 3.0},
        ),
        (
            {
                "kind": "event",
                "event": "price",
                "when": {"symbol": "AAPL", "direction": "fall", "value": 7.5},
                "limit": "day",
            },
            {"symbol": "AAPL", "direction": "fall", "value": 7.5},
        ),
    ],
)
def test_event_trigger_validates_each_ui_shape(trigger, expected_when):
    assert validate_trigger(trigger)["when"] == expected_when


def test_balance_event_accepts_a_signed_liability_threshold():
    trigger = {
        "kind": "event",
        "event": "balance",
        "when": {"account": "acc_card", "direction": "below", "amount": "-500"},
        "limit": "each",
    }

    assert validate_trigger(trigger)["when"]["amount"] == -500.0


@pytest.mark.parametrize("event", ["due", "sync"])
def test_the_retired_due_and_sync_events_are_refused(event):
    """Automations react to a transaction, a balance crossing or a price move — the "planned
    payment is due" and "connector sync fails" triggers were removed."""
    with pytest.raises(ValueError):
        validate_trigger({"kind": "event", "event": event, "when": {}, "limit": "each"})


def test_transaction_event_preserves_migrated_predicates_and_sequence():
    trigger = {
        "kind": "event",
        "event": "txn",
        "when": {
            "operation": "create",
            "description_contains": "coffee",
            "keywords": ["coffee", "cafe"],
            "description_regex": None,
            "memo_contains": None,
            "memo_keywords": ["claim"],
            "memo_regex": "#claim$",
            "account_id": "acc_card",
            "kind": "expense",
            "direction": "debit",
            "currency": "SGD",
            "min_amount": 2.5,
            "max_amount": 50.0,
        },
        "limit": "each",
        "sequence": {"group": "migrated-transaction-rules", "priority": -10, "stop": True},
    }

    assert validate_trigger(trigger) == trigger


@pytest.mark.parametrize(
    "trigger",
    [
        {
            "kind": "event",
            "event": "balance",
            "when": {"account": "acc_cash", "direction": "below", "amount": "oops"},
            "limit": "each",
        },
        {
            "kind": "event",
            "event": "price",
            "when": {"symbol": "AAPL", "direction": "move", "value": "oops"},
            "limit": "each",
        },
    ],
)
def test_event_trigger_rejects_malformed_runtime_numbers(trigger):
    with pytest.raises(ValueError):
        validate_trigger(trigger)


@pytest.mark.parametrize(
    "trigger",
    [
        {
            "kind": "event",
            "event": "balance",
            "when": {"direction": "below", "amount": 100},
            "limit": "each",
        },
        {
            "kind": "event",
            "event": "balance",
            "when": {"account": "acc_cash", "direction": "sideways", "amount": 100},
            "limit": "each",
        },
        {
            "kind": "event",
            "event": "price",
            "when": {"symbol": "AAPL", "direction": "flat", "value": 5},
            "limit": "each",
        },
        {
            "kind": "event",
            "event": "txn",
            "when": {"min_amount": 100, "max_amount": 10},
            "limit": "each",
        },
        {
            "kind": "event",
            "event": "txn",
            "when": {"direction": "sideways"},
            "limit": "each",
        },
    ],
)
def test_event_trigger_rejects_missing_out_of_range_or_unknown_enum_values(trigger):
    with pytest.raises(ValueError):
        validate_trigger(trigger)


@pytest.mark.parametrize(
    ("event", "when"),
    [
        ("txn", {"operation": ["create", 1]}),
        ("txn", {"keywords": "coffee"}),
        ("txn", {"description_regex": "["}),
        ("balance", {"account": "acc_cash", "direction": "below", "amount": True}),
        ("price", {"symbol": 123, "direction": "above", "value": 200}),
    ],
)
def test_event_trigger_rejects_invalid_predicate_types(event, when):
    with pytest.raises(ValueError):
        validate_trigger({"kind": "event", "event": event, "when": when, "limit": "each"})


@pytest.mark.parametrize(
    ("event", "when"),
    [
        ("txn", {"surprise": True}),
        ("balance", {"account": "acc_cash", "direction": "below", "amount": 100, "surprise": True}),
        ("price", {"symbol": "AAPL", "direction": "above", "value": 200, "surprise": True}),
    ],
)
def test_event_trigger_rejects_unknown_predicate_keys(event, when):
    with pytest.raises(ValueError):
        validate_trigger({"kind": "event", "event": event, "when": when, "limit": "each"})


def test_every_trigger_shape_the_tool_description_advertises_actually_validates():
    """Owner incident (2026-08-09): asked for a bill reminder, Lucius reached
    create_automation and then burned FOUR failed calls guessing the trigger keys —
    ``type`` at top level, then ``kind``+``datetime``, then ``kind``+``schedule_type``.

    It could not have known. ``trigger`` is ``Dict[str, Any]`` with ``bare_object_schema``,
    so the field description is the ONLY channel carrying the key names, and the old
    one-liner ("Manual, schedule, or event trigger object") named none of them. The
    validator's errors were the only other feedback and taught nothing either
    ("unknown automation schedule type None" — which key? what values?).

    Both were fixed. This pins them together: every shape ``_TRIGGER_DESC`` advertises must
    validate, so the description cannot drift from the validator it documents.
    """
    advertised = [
        {"kind": "manual"},
        {"kind": "schedule", "type": "once", "at": "2099-08-19T09:00:00+08:00"},
        {"kind": "schedule", "type": "daily", "time": "09:00"},
        {"kind": "schedule", "type": "weekdays", "time": "09:00", "tzid": "Asia/Singapore"},
        {"kind": "schedule", "type": "weekly", "time": "09:00", "day": "mon"},
        {"kind": "schedule", "type": "monthly", "time": "09:00", "day": 19},
        {"kind": "schedule", "type": "interval", "seconds": 60},
        {"kind": "schedule", "type": "rrule", "rrule": "FREQ=MONTHLY;BYDAY=3FR"},
        {
            "kind": "schedule",
            "type": "rrule",
            "rrule": "FREQ=WEEKLY;INTERVAL=2",
            "dtstart": "2099-08-19T09:00:00+08:00",
            "tzid": "Asia/Singapore",
        },
        {"kind": "event", "event": "txn", "when": {"operation": "create"}, "limit": "each"},
    ]
    for trigger in advertised:
        validate_trigger(trigger)  # raises ValueError if the description lied


@pytest.mark.parametrize(
    "trigger, must_mention",
    [
        ({"kind": "schedule", "datetime": "2099-01-01T09:00:00+08:00"}, "'type'"),
        ({"kind": "schedule", "schedule_type": "once"}, "once"),
        ({"type": "schedule"}, "manual, schedule, or event"),
    ],
)
def test_a_wrong_trigger_guess_is_told_what_the_right_one_looks_like(trigger, must_mention):
    """The error text is a retrying model's ONLY feedback channel — these are the three
    guesses seen live, each of which previously returned a message naming neither the key
    it wanted nor a valid value."""
    with pytest.raises(ValueError) as excinfo:
        validate_trigger(trigger)
    assert must_mention in str(excinfo.value)


def test_a_job_that_fell_behind_resumes_instead_of_replaying_every_missed_day():
    """Repro (five days blocked): "Daily Trades Summary" parked on a notify
    approval card for five days. When the approval finally resolved, the automation did not
    resume — it REPLAYED. Five separate paid runs fired back to back, each reporting on a
    different stale date ("no IBKR trades were logged for the anchor date"), each notifying.

    The cause is that ``after`` (the run's own scheduled_for) was doing two jobs at once.
    Anchoring on it is right — it is what stops a daily 09:00 job drifting later by each
    run's duration — but on its own it advances exactly ONE occurrence, so a five-day-late
    run got a next_run_at four days in the PAST. The scheduler reads that as due, fires,
    advances one more day, and repeats until it catches up to real time.

    ``not_before`` splits the two jobs apart: keep the grid, skip the backlog.
    """
    scheduled = datetime(2025, 2, 4, 9, 0, tzinfo=ZoneInfo("UTC"))
    resolved = datetime(2025, 2, 9, 14, 30, tzinfo=ZoneInfo("UTC"))
    trigger = {"kind": "schedule", "type": "daily", "time": "09:00", "tzid": "UTC"}

    # The bug: anchored on scheduled_for alone, the "next" run is four days in the past.
    assert compute_next_run(trigger, scheduled) == "2025-02-05T09:00:00+00:00"

    # Fixed: one step to the next slot that has not already passed. No replay.
    assert compute_next_run(trigger, scheduled, not_before=resolved) == "2025-02-10T09:00:00+00:00"


def test_catching_up_does_not_re_phase_an_interval_schedule():
    """``interval`` is the one type whose phase IS ``after``, so it cannot just start from
    the floor — that would silently re-time the cadence to whenever the backlog happened to
    clear. It jumps a whole number of periods instead: still on the original grid, still
    strictly future."""
    anchor = datetime(2026, 8, 4, 8, 0, tzinfo=ZoneInfo("UTC"))
    late = datetime(2026, 8, 9, 14, 30, tzinfo=ZoneInfo("UTC"))
    trigger = {"kind": "schedule", "type": "interval", "seconds": 3600}

    # Compared as instants: the trigger carries no tzid, so the string renders in server
    # local time and the offset is not what is under test.
    result = datetime.fromisoformat(compute_next_run(trigger, anchor, not_before=late))
    assert result == datetime(2026, 8, 9, 15, 0, tzinfo=ZoneInfo("UTC"))  # :00 grid, not 15:30
    assert result > late

    # Not behind → unchanged: exactly one period on, never a skip.
    one_period = datetime(2026, 8, 4, 9, 0, tzinfo=ZoneInfo("UTC"))
    assert (
        datetime.fromisoformat(compute_next_run(trigger, anchor, not_before=anchor)) == one_period
    )
    assert datetime.fromisoformat(compute_next_run(trigger, anchor)) == one_period


@pytest.mark.parametrize(
    "trigger, expected",
    [
        (
            {"kind": "schedule", "type": "weekdays", "time": "09:00", "tzid": "UTC"},
            "2026-08-10T09:00:00+00:00",  # Aug 9 2026 is a Sunday
        ),
        (
            {"kind": "schedule", "type": "weekly", "time": "09:00", "day": "tue", "tzid": "UTC"},
            "2026-08-11T09:00:00+00:00",
        ),
        (
            {"kind": "schedule", "type": "monthly", "time": "09:00", "day": 4, "tzid": "UTC"},
            "2026-09-04T09:00:00+00:00",
        ),
        (
            {"kind": "schedule", "type": "rrule", "rrule": "FREQ=DAILY", "tzid": "UTC"},
            "2026-08-10T09:00:00+00:00",
        ),
    ],
)
def test_every_recurring_type_skips_the_backlog_rather_than_walking_it(trigger, expected):
    """Not just daily — any recurring type can fall behind the same way."""
    scheduled = datetime(2026, 8, 4, 9, 0, tzinfo=ZoneInfo("UTC"))
    resolved = datetime(2026, 8, 9, 14, 30, tzinfo=ZoneInfo("UTC"))
    result = compute_next_run(trigger, scheduled, created_at=scheduled, not_before=resolved)
    assert result == expected
    assert datetime.fromisoformat(result) > resolved


def test_a_schedule_that_is_on_time_is_completely_unaffected_by_the_floor():
    """The floor must be inert in the normal case — it only ever removes past slots."""
    scheduled = datetime(2026, 8, 4, 9, 0, tzinfo=ZoneInfo("UTC"))
    just_after = datetime(2026, 8, 4, 9, 0, 12, tzinfo=ZoneInfo("UTC"))
    for trigger in [
        {"kind": "schedule", "type": "daily", "time": "09:00", "tzid": "UTC"},
        {"kind": "schedule", "type": "weekdays", "time": "09:00", "tzid": "UTC"},
        {"kind": "schedule", "type": "weekly", "time": "09:00", "day": "tue", "tzid": "UTC"},
        {"kind": "schedule", "type": "monthly", "time": "09:00", "day": 4, "tzid": "UTC"},
        {"kind": "schedule", "type": "interval", "seconds": 3600},
    ]:
        assert compute_next_run(trigger, scheduled, not_before=just_after) == compute_next_run(
            trigger, scheduled
        ), trigger


def test_a_one_off_that_was_missed_does_not_resurrect():
    """``once`` has no next slot to skip to — a missed one-off pauses (next_run_at None)
    rather than firing days late about something that already happened."""
    trigger = {"kind": "schedule", "type": "once", "at": "2026-08-04T09:00:00+00:00"}
    scheduled = datetime(2026, 8, 4, 9, 0, tzinfo=ZoneInfo("UTC"))
    resolved = datetime(2026, 8, 9, 14, 30, tzinfo=ZoneInfo("UTC"))
    assert compute_next_run(trigger, scheduled, not_before=resolved) is None


@pytest.mark.parametrize(
    ("trigger", "expected"),
    [
        # tz-less wall-clock slot: the SAME 09:00, re-read on the machine's new clock.
        (
            {"kind": "schedule", "type": "monthly", "day": 28, "time": "09:00"},
            "2026-09-28T09:00:00+07:00",
        ),
        # A pinned zone and an interval are instants — the stamp stands.
        (
            {
                "kind": "schedule",
                "type": "monthly",
                "day": 28,
                "time": "09:00",
                "tzid": "Asia/Singapore",
            },
            "2026-09-28T09:00:00+08:00",
        ),
        ({"kind": "schedule", "type": "interval", "seconds": 3600}, "2026-09-28T08:00:00+07:00"),
    ],
)
def test_ran_slot_floor_places_the_slot_on_the_current_clock(trigger, expected, monkeypatch):
    import backend.services.automations.schedules as schedules

    monkeypatch.setattr(schedules, "_local_tz", lambda: ZoneInfo("Asia/Ho_Chi_Minh"))
    ran = datetime.fromisoformat("2026-09-28T09:00:00+08:00")

    assert schedules.ran_slot_floor(trigger, ran) == datetime.fromisoformat(expected)


def test_ran_slot_floor_reads_a_naive_legacy_stamp_as_local(monkeypatch):
    import backend.services.automations.schedules as schedules

    monkeypatch.setattr(schedules, "_local_tz", lambda: ZoneInfo("Asia/Ho_Chi_Minh"))
    trigger = {"kind": "schedule", "type": "monthly", "day": 1, "time": "09:00"}

    floor = schedules.ran_slot_floor(trigger, datetime.fromisoformat("2026-10-01T09:00:00"))

    assert floor == datetime.fromisoformat("2026-10-01T09:00:00+07:00")
