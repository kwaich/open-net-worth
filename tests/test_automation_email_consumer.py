"""An Automation run owns its own IMAP cursor.

``fetch_emails`` reads incrementally per CONSUMER KEY. Before this, only ``run_agent`` ever
bound one, so every Automation calling ``fetch_emails`` shared ``DEFAULT_CONSUMER`` with
interactive chat: asking Lucius to check email in a conversation silently consumed messages
the Automation was going to book, and the cursor never rewinds.
"""

from __future__ import annotations

import pytest

from backend.ai import assistant
from backend.ai.automation_runner_adapter import automation_runner
from backend.core.automation_runner import AutomationRunnerRequest
from backend.core.email_consumer import DEFAULT_CONSUMER, get_email_consumer


def _request(session_id: str, automation_id: str = "automation_email") -> AutomationRunnerRequest:
    return AutomationRunnerRequest(
        automation_id=automation_id,
        run_id="run_email",
        session_id=session_id,
        title="Log transactions from email",
        instructions="Fetch new emails and log what you find.",
        model="model-a",
    )


@pytest.fixture
def quiet_session(monkeypatch):
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(assistant, "save_session_durable", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_the_run_binds_its_own_email_consumer(monkeypatch, quiet_session):
    session_id = "automation-run-email-a"
    assistant.SESSIONS[session_id] = [{"role": "user", "content": "Fetch"}]
    seen: dict[str, str] = {}

    async def fake_run(*args, **kwargs):
        seen["consumer"] = get_email_consumer()
        assistant.SESSIONS[session_id].append({"role": "assistant", "content": "Done"})
        yield {"type": "turn_done", "usage": {"model": "model-a"}}

    monkeypatch.setattr(assistant, "run", fake_run)
    await automation_runner(_request(session_id))

    assert seen["consumer"] == "automation:automation_email"


@pytest.mark.asyncio
async def test_two_automations_do_not_share_a_cursor(monkeypatch, quiet_session):
    """The whole point: one Automation draining email must not starve another."""
    seen: list[str] = []

    async def fake_run(session_id, *args, **kwargs):
        seen.append(get_email_consumer())
        assistant.SESSIONS[session_id].append({"role": "assistant", "content": "Done"})
        yield {"type": "turn_done", "usage": {"model": "model-a"}}

    monkeypatch.setattr(assistant, "run", fake_run)
    for name in ("alpha", "beta"):
        sid = f"automation-run-email-{name}"
        assistant.SESSIONS[sid] = [{"role": "user", "content": "Fetch"}]
        await automation_runner(_request(sid, automation_id=name))

    assert seen == ["automation:alpha", "automation:beta"]


@pytest.mark.asyncio
async def test_the_consumer_is_restored_after_the_run(monkeypatch, quiet_session):
    """Interactive chat that follows must be back on the shared default cursor."""
    session_id = "automation-run-email-restore"
    assistant.SESSIONS[session_id] = [{"role": "user", "content": "Fetch"}]

    async def fake_run(*args, **kwargs):
        assistant.SESSIONS[session_id].append({"role": "assistant", "content": "Done"})
        yield {"type": "turn_done", "usage": {"model": "model-a"}}

    monkeypatch.setattr(assistant, "run", fake_run)
    before = get_email_consumer()
    await automation_runner(_request(session_id))

    assert get_email_consumer() == before == DEFAULT_CONSUMER


@pytest.mark.asyncio
async def test_the_consumer_is_restored_when_the_run_fails(monkeypatch, quiet_session):
    """A leaked consumer key would silently redirect the NEXT turn's cursor."""
    session_id = "automation-run-email-boom"
    assistant.SESSIONS[session_id] = [{"role": "user", "content": "Fetch"}]

    async def fake_run(*args, **kwargs):
        yield {"type": "text_delta", "text": "starting"}
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(assistant, "run", fake_run)
    result = await automation_runner(_request(session_id))

    assert result.status == "failed"
    assert get_email_consumer() == DEFAULT_CONSUMER
