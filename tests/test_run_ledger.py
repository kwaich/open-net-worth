"""
Unit tests for services/run_ledger.py.

Covers: status derivation, redaction, streak-dedup, recovered note, systemic grouping.
No LLM / network calls — notify is monkeypatched throughout.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.db.run_history_store import RunHistoryStore

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Redirect run_ledger's shared store to a tmp_path instance."""
    import backend.services.run_ledger as rl

    store = RunHistoryStore()
    store._path = tmp_path / "run_history.json"
    monkeypatch.setattr(rl, "_store", store)
    # Clear in-memory group-suppress cache so tests are independent.
    rl._group_suppress.clear()
    return store


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _record(status="ok", entity_kind="agent", entity_id="a1", entity_name="Monitor", error=None):
    import backend.services.run_ledger as rl

    return run(
        rl.record_run(
            entity_kind=entity_kind,
            entity_id=entity_id,
            entity_name=entity_name,
            started_at="2026-06-11T10:00:00",
            status=status,
            summary=f"run status={status}",
            trigger="manual",
            error=error,
        )
    )


# ── Status derivation ─────────────────────────────────────────────────────────


def test_parse_status_ok():
    from backend.services.run_ledger import _parse_status

    assert _parse_status("ok") == "ok"
    assert _parse_status("ok: done") == "ok"


def test_parse_status_error():
    from backend.services.run_ledger import _parse_status

    assert _parse_status("error: boom") == "error"
    assert _parse_status("error") == "error"


def test_parse_status_skipped():
    from backend.services.run_ledger import _parse_status

    assert _parse_status("skipped: disabled") == "skipped"


def test_parse_status_empty_defaults_ok():
    from backend.services.run_ledger import _parse_status

    assert _parse_status("") == "ok"
    assert _parse_status(None) == "ok"


# ── Redaction ─────────────────────────────────────────────────────────────────


def test_redact_strips_sk_key():
    from backend.services.run_ledger import _redact

    text = "error: invalid OpenAI API key sk-abc123XYZ789"
    result = _redact(text)
    assert "sk-abc123XYZ789" not in result
    assert "[api-key]" in result


def test_redact_strips_bearer_token():
    """Bearer prefix triggers the Bearer pattern specifically (not the api-key pattern)."""
    from backend.services.run_ledger import _redact

    text = "Authorization: Bearer abcdef123456xyz789"
    result = _redact(text)
    assert "abcdef123456xyz789" not in result
    assert "Bearer [token]" in result


def test_redact_strips_bare_github_token():
    """REGRESSION: bare ghp_ token (no Bearer prefix) must be redacted."""
    from backend.services.run_ledger import _redact

    token = "ghp_" + "A" * 36
    text = f"git push failed: token {token} rejected"
    result = _redact(text)
    assert token not in result
    assert "[api-key]" in result


def test_redact_strips_github_pat_token():
    """github_pat_ fine-grained PAT must be redacted."""
    from backend.services.run_ledger import _redact

    token = "github_pat_" + "B" * 22
    text = f"authentication failed using {token}"
    result = _redact(text)
    assert token not in result
    assert "[api-key]" in result


def test_redact_strips_stripe_live_key():
    """Stripe sk_live_ key must be redacted."""
    from backend.services.run_ledger import _redact

    token = "sk_live_" + "C" * 24
    text = f"stripe charge error with key {token}"
    result = _redact(text)
    assert token not in result
    assert "[api-key]" in result


def test_redact_strips_slack_xoxb_token():
    """Slack xoxb- bot token must be redacted."""
    from backend.services.run_ledger import _redact

    token = "xoxb-12345678-abcdefghij"
    text = f"slack api call failed: invalid token {token}"
    result = _redact(text)
    assert token not in result
    assert "[api-key]" in result


def test_redact_strips_url_query():
    from backend.services.run_ledger import _redact

    text = "fetched https://api.openai.com/v1/chat/completions?key=secret123"
    result = _redact(text)
    assert "secret123" not in result
    assert "https://api.openai.com" in result


def test_redact_strips_email():
    from backend.services.run_ledger import _redact

    text = "contact user@example.com for help"
    result = _redact(text)
    assert "user@example.com" not in result
    assert "[email]" in result


def test_redact_empty_passthrough():
    from backend.services.run_ledger import _redact

    assert _redact("") == ""
    assert _redact(None) is None


def test_redact_strips_key_eq_value():
    from backend.services.run_ledger import _redact

    text = "POST /auth?api_key=sk-test9999secret&foo=bar"
    result = _redact(text)
    assert "sk-test9999secret" not in result


# ── Record on success — no notify ─────────────────────────────────────────────


def test_record_ok_no_notify(isolated_store):
    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        row = _record(status="ok")

    assert row["status"] == "ok"
    assert len(notified) == 0


def test_record_ok_appended_to_store(isolated_store):
    _record(status="ok")
    rows = isolated_store.for_entity("agent", "a1")
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"


# ── First failure triggers notify ─────────────────────────────────────────────


def test_record_first_error_emits_one_notify(isolated_store):
    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        _record(status="error", error="error: invalid API key sk-abc123")

    assert len(notified) == 1
    msg = notified[0]
    # Redacted — no raw key in outbound message
    assert "sk-abc123" not in msg
    assert "Monitor" in msg


def test_error_message_contains_entity_name(isolated_store):
    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        _record(status="error", entity_name="TX Logger", error="error: boom")

    assert "TX Logger" in notified[0]


def test_full_error_stored_not_redacted(isolated_store):
    """The full error is stored locally; only outbound notify is redacted."""
    with patch("backend.services.run_ledger.notify_svc.notify", AsyncMock(return_value={})):
        row = _record(status="error", error="error: key sk-abc123supersecret failed")

    assert "sk-abc123supersecret" in row["error"]


# ── REGRESSION: two consecutive errors → exactly ONE alert ───────────────────


def test_two_consecutive_errors_one_notify(isolated_store):
    """Core streak-dedup regression: two consecutive failures emit exactly one alert."""
    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        _record(status="error", error="error: connection refused")
        _record(status="error", error="error: connection refused")

    assert len(notified) == 1


def test_streak_fail_count_increments(isolated_store):
    with patch("backend.services.run_ledger.notify_svc.notify", AsyncMock(return_value={})):
        _record(status="error", error="error: boom")
        _record(status="error", error="error: boom")

    streak = isolated_store.streak_state("agent", "a1")
    assert streak["fail_count"] == 2
    assert streak["failing"] is True


# ── Recovered note ────────────────────────────────────────────────────────────


def test_recovered_note_after_failure_streak(isolated_store):
    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        _record(status="error", error="error: bad key")
        _record(status="ok")

    assert len(notified) == 2
    assert any(
        "normally again" in m or "recovered" in m.lower() or "running normally" in m
        for m in notified
    )


def test_streak_reset_on_recovery(isolated_store):
    with patch("backend.services.run_ledger.notify_svc.notify", AsyncMock(return_value={})):
        _record(status="error", error="error: bad key")
        _record(status="ok")

    streak = isolated_store.streak_state("agent", "a1")
    assert streak["failing"] is False
    assert streak["fail_count"] == 0


def test_no_recovered_note_on_first_success(isolated_store):
    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        _record(status="ok")

    assert len(notified) == 0


# ── Skipped does not reset streak ────────────────────────────────────────────


def test_skipped_does_not_reset_streak(isolated_store):
    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        _record(status="error", error="error: boom")
        _record(status="skipped")  # should NOT reset streak

    streak = isolated_store.streak_state("agent", "a1")
    assert streak["failing"] is True
    assert streak["fail_count"] == 1
    assert len(notified) == 1  # only the first-failure alert


def test_error_after_skipped_still_suppressed(isolated_store):
    """error + skipped + error → still ONE alert (skipped doesn't break the streak)."""
    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        _record(status="error", error="error: boom")
        _record(status="skipped")
        _record(status="error", error="error: boom again")

    assert len(notified) == 1


def test_ok_after_error_then_skipped_still_recovered(isolated_store):
    """error + skipped + ok → recovered note (streak still active through skipped)."""
    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        _record(status="error", error="error: boom")
        _record(status="skipped")
        _record(status="ok")

    assert len(notified) == 2  # failure + recovered


# ── Per-entity independence ───────────────────────────────────────────────────


def test_streak_independent_per_entity(isolated_store):
    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        _record(status="error", entity_id="a1", error="error: A down")
        _record(status="error", entity_id="a2", error="error: B down")

    # Each entity's first failure fires independently
    assert len(notified) == 2


# ── Systemic grouping ─────────────────────────────────────────────────────────


def test_systemic_grouping_two_entities_same_cause(isolated_store):
    """Two agents fail within the window with same root cause → ONE grouped alert."""
    from datetime import datetime, timezone

    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    # Use the current time so it falls within the 5-minute window.
    now_iso = datetime.now(timezone.utc).isoformat()
    redacted_cause = "error: connection refused"
    isolated_store.set_streak_state(
        "agent",
        "a1",
        {
            "failing": True,
            "fail_count": 1,
            "first_error_at": now_iso,
            "last_error_redacted": redacted_cause,
        },
    )

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        import backend.services.run_ledger as rl

        run(
            rl.record_run(
                entity_kind="agent",
                entity_id="a2",
                entity_name="Agent B",
                started_at=now_iso,
                status="error",
                summary="run status=error",
                trigger="manual",
                error=redacted_cause,  # same root cause → grouping fires
            )
        )

    # Should emit one alert (grouped, not per-entity)
    assert len(notified) == 1
    msg = notified[0]
    # Grouped message mentions count or multiple entities
    assert "automation" in msg.lower() or "failing" in msg.lower()


def test_distinct_causes_separate_alerts(isolated_store):
    """Distinct root causes → separate per-entity alerts, no grouping."""
    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        _record(
            status="error", entity_id="a1", entity_name="AgentA", error="error: connection refused"
        )
        _record(
            status="error", entity_id="a2", entity_name="AgentB", error="error: rate limit exceeded"
        )

    assert len(notified) == 2


# ── Notify best-effort isolation ──────────────────────────────────────────────


def test_notify_failure_does_not_raise(isolated_store):
    """A failing notify must not propagate — run record is still written."""

    async def raising_notify(msg, **kw):
        raise RuntimeError("channel offline")

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=raising_notify):
        row = _record(status="error", error="error: boom")

    assert row["status"] == "error"
    assert len(isolated_store.for_entity("agent", "a1")) == 1


# ── SECURITY: ReDoS regression ───────────────────────────────────────────────


def test_redact_adversarial_long_string_fast():
    """ReDoS regression: _redact() on a 200K-char adversarial string (long alnum/dot run
    that fails the TLD anchor) must complete well under 1 second."""
    import time

    from backend.services.run_ledger import _redact

    # A string that contains long alnum/dot sequences that look like partial email addresses
    # but fail the TLD constraint — the classic quadratic backtrack trigger.
    adversarial = ("a" * 64 + "." + "b" * 63 + ".") * 1000  # ~200K chars
    start = time.monotonic()
    result = _redact(adversarial)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"_redact took {elapsed:.2f}s on adversarial input (ReDoS?)"
    # Must not raise
    assert isinstance(result, str)


# ── SECURITY: Widened redaction patterns ─────────────────────────────────────


def test_redact_aws_access_key():
    from backend.services.run_ledger import _redact

    text = "AWS key is AKIAIOSFODNN7EXAMPLE and secret follows"
    result = _redact(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in result
    assert "[aws-key]" in result


def test_redact_aws_access_key_asia_prefix():
    from backend.services.run_ledger import _redact

    # ASIA + exactly 16 uppercase alnum chars = 20 chars total (STS session key shape)
    text = "session key ASIAIOSFODNN7EXAMPLE from STS"
    result = _redact(text)
    assert "ASIAIOSFODNN7EXAMPLE" not in result
    assert "[aws-key]" in result


def test_redact_google_aiza_key():
    from backend.services.run_ledger import _redact

    # AIza + 35 alnum chars
    key = "AIza" + "A" * 35
    text = f"google api key={key} failed"
    result = _redact(text)
    assert key not in result
    assert "[google-key]" in result


def test_redact_gitlab_glpat_token():
    from backend.services.run_ledger import _redact

    text = "token glpat-xxxxxxxxxxxxxxxxxxxx was revoked"
    result = _redact(text)
    assert "glpat-xxxxxxxxxxxxxxxxxxxx" not in result
    assert "[gitlab-token]" in result


def test_redact_password_eq_field():
    from backend.services.run_ledger import _redact

    text = "connection string password=supersecret123 rejected"
    result = _redact(text)
    assert "supersecret123" not in result
    assert "[credential]=***" in result


def test_redact_passwd_eq_field():
    from backend.services.run_ledger import _redact

    text = "db uri: postgres://host/db?passwd=hunter2secret"
    result = _redact(text)
    assert "hunter2secret" not in result


def test_redact_pwd_eq_field():
    from backend.services.run_ledger import _redact

    text = "config has pwd=myPassword123 in it"
    result = _redact(text)
    assert "myPassword123" not in result


def test_redact_basic_auth_url_with_ip():
    from backend.services.run_ledger import _redact

    text = "connecting to http://admin:p4ssw0rd@192.168.1.1/api"
    result = _redact(text)
    assert "p4ssw0rd" not in result
    assert "admin" not in result or "[url-with-credentials]" in result


def test_redact_basic_auth_url_with_hostname():
    from backend.services.run_ledger import _redact

    text = "upstream https://user:s3cr3t@internal.corp.example.com/v1/data"
    result = _redact(text)
    assert "s3cr3t" not in result


# ── Fix 3: Grouped alert uses entity NAMES not IDs ────────────────────────────


def test_systemic_grouping_uses_entity_names(isolated_store):
    """Grouped alert must contain the human-readable names of affected peers, not their ids."""
    from datetime import datetime, timezone

    notified: list[str] = []

    async def mock_notify(msg, **kw):
        notified.append(msg)
        return {}

    now_iso = datetime.now(timezone.utc).isoformat()
    redacted_cause = "error: bad credentials"
    # Seed a peer streak record WITH entity_name denormalized
    isolated_store.set_streak_state(
        "agent",
        "agent-uuid-1",
        {
            "entity_name": "Billing Monitor",
            "failing": True,
            "fail_count": 1,
            "first_error_at": now_iso,
            "last_error_redacted": redacted_cause,
        },
    )

    with patch("backend.services.run_ledger.notify_svc.notify", side_effect=mock_notify):
        import backend.services.run_ledger as rl

        run(
            rl.record_run(
                entity_kind="agent",
                entity_id="agent-uuid-2",
                entity_name="Portfolio Scanner",
                started_at=now_iso,
                status="error",
                summary="run status=error",
                trigger="manual",
                error=redacted_cause,
            )
        )

    assert len(notified) == 1
    msg = notified[0]
    # Must contain the human name of the NEW entity and the PEER entity
    assert "Portfolio Scanner" in msg
    assert "Billing Monitor" in msg
    # Must NOT contain raw UUIDs as the sole identifiers (names must be present)
    assert "agent-uuid-1" not in msg or "Billing Monitor" in msg


def test_systemic_grouping_streak_stores_entity_name(isolated_store):
    """set_streak_state via _handle_streak_and_notify must persist entity_name."""
    from unittest.mock import AsyncMock

    with patch("backend.services.run_ledger.notify_svc.notify", AsyncMock(return_value={})):
        _record(status="error", entity_id="a1", entity_name="My Agent", error="error: down")

    streak = isolated_store.streak_state("agent", "a1")
    assert streak is not None
    assert streak.get("entity_name") == "My Agent"


# ── Fix 4: trigger_title enrichment ─────────────────────────────────────────


def test_trigger_title_schedule_returns_task_title(isolated_store, tmp_path, monkeypatch):
    """automation:<id> trigger → trigger_title = that Automation's name."""
    import backend.services.run_ledger as rl
    from backend.db import automation_store as automation_mod

    # AutomationStore is DB-backed now (plan 4d task 2) — it shares the same process-wide
    # default engine `isolated_store` already relies on for RunHistoryStore, cleared after
    # every test by the suite's autouse Tier-2 clear (tests/conftest.py); no per-store path
    # to repoint any more.
    automation_mod.AutomationStore().create(
        {
            "id": "task-abc",
            "name": "Daily Portfolio Check",
            "instructions": "Check the portfolio.",
            "trigger": {"kind": "manual"},
        }
    )

    isolated_store.append(
        {
            "kind": "agent",
            "entity_id": "a1",
            "entity_name": "Test Agent",
            "started_at": "2026-06-11T10:00:00",
            "status": "ok",
            "summary": "done",
            "trigger": "automation:task-abc",
        }
    )

    runs = rl.list_runs("agent", "a1")

    assert len(runs) == 1
    assert runs[0]["trigger_title"] == "Daily Portfolio Check"


def test_trigger_title_manual_returns_null(isolated_store, tmp_path):
    """A trigger that is not `automation:<id>` → trigger_title = None."""
    import backend.services.run_ledger as rl

    isolated_store.append(
        {
            "kind": "agent",
            "entity_id": "a1",
            "entity_name": "Test Agent",
            "started_at": "2026-06-11T10:00:00",
            "status": "ok",
            "summary": "done",
            "trigger": "manual",
        }
    )

    runs = rl.list_runs("agent", "a1")
    assert len(runs) == 1
    assert runs[0]["trigger_title"] is None


def test_trigger_title_deleted_task_returns_null(isolated_store, tmp_path, monkeypatch):
    """automation:<id> where the Automation no longer exists → trigger_title = None."""
    import backend.services.run_ledger as rl

    isolated_store.append(
        {
            "kind": "agent",
            "entity_id": "a1",
            "entity_name": "Test Agent",
            "started_at": "2026-06-11T10:00:00",
            "status": "ok",
            "summary": "done",
            "trigger": "automation:nonexistent",
        }
    )

    runs = rl.list_runs("agent", "a1")

    assert len(runs) == 1
    assert runs[0]["trigger_title"] is None


# ── Fix 7: _group_suppress pruning ───────────────────────────────────────────


def test_group_suppress_prunes_expired_entries(isolated_store):
    """_prune_group_suppress removes entries older than the window."""
    from backend.services.run_ledger import _group_suppress, _prune_group_suppress

    _group_suppress.clear()
    # Insert an old entry well outside any window
    _group_suppress["old-cause"] = "2020-01-01T00:00:00+00:00"
    _group_suppress["recent-cause"] = "2099-01-01T00:00:00+00:00"

    _prune_group_suppress("2026-01-01T00:00:00+00:00")

    assert "old-cause" not in _group_suppress
    assert "recent-cause" in _group_suppress
