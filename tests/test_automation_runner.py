from __future__ import annotations

import json

import pytest

import backend.ai.automation_runner_adapter as adapter
from backend.ai import assistant, pending_injection
from backend.ai.automation_runner_adapter import (
    _pending_ids,
    automation_cleanup,
    automation_runner,
    materialize_automation_session,
    stream_automation_followup,
)
from backend.core.automation_runner import AutomationRunnerRequest, AutomationRunnerResult
from backend.core.errors import DataIntegrityError
from backend.db.automation_run_store import AutomationRunStore
from backend.db.skill_store import SkillStore
from backend.services.automations.runs import (
    begin_automation_continuation,
    reconcile_automation_continuation,
    recover_automation_runs,
)


def test_legacy_run_backfill_creates_an_honest_durable_conversation(db_isolated):
    # Sessions are DB-backed now (Wave 5E, JSON->DB) — db_isolated already isolates the
    # `sessions` table; no more SESSIONS_DIR to monkeypatch.
    assistant.SESSIONS.clear()

    run = AutomationRunStore().start(
        {
            "automation_id": "automation_missing",
            "session_id": None,
            "summary": "Imported 3 rows",
            "context": {"source": "legacy"},
            "files": ["receipt.csv"],
            "cost_usd": 0.12,
        }
    )
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})

    outcome = recover_automation_runs(materialize_session=materialize_automation_session)

    updated = AutomationRunStore().find_by_id(run["id"])
    assert outcome["backfilled"] == 1
    assert updated["session_id"] == f"automation-run-{run['id']}"
    from backend.db.session_store import SessionStore

    transcript = SessionStore().get_transcript(updated["session_id"])
    rendered = json.dumps(transcript)
    assert "Imported 3 rows" in rendered
    assert "legacy" in rendered
    assert "receipt.csv" in rendered
    assert "0.12" in rendered
    assert "automation_missing" not in rendered


@pytest.mark.parametrize(
    ("entry_path", "model", "base_url"),
    [
        ("initial", "small-model", "https://small.invalid"),
        ("followup", "large-model", "https://large.invalid"),
        ("resolution", "small-model", "https://small.invalid"),
    ],
)
async def test_all_automation_prompt_entries_pin_one_model_context(
    monkeypatch, entry_path, model, base_url
):
    provider = object()
    context = assistant.ModelTurnContext(
        model=model,
        provider_key="provider-a",
        transport="openai_compatible",
        base_url=base_url,
        provider=provider,
    )
    resolved = []
    prompts = []
    calls = []

    def resolve(override):
        resolved.append(override)
        return context

    def prompt(**kwargs):
        prompts.append(kwargs)
        return f"prompt:{kwargs['model']}:{kwargs['base_url']}"

    async def fake_run(*args, **kwargs):
        calls.append(("run", kwargs))
        yield {"type": "turn_done", "usage": {"model": model}}

    async def fake_resolve(*args, **kwargs):
        calls.append(("resolution", kwargs))
        yield {"type": "turn_done", "usage": {"model": model}}

    request = AutomationRunnerRequest(
        automation_id="automation-a",
        run_id="run-a",
        session_id="automation-pinned-context",
        title="Pinned",
        instructions="Run it",
        model="configured-model",
    )
    monkeypatch.setattr(assistant, "resolve_model_context", resolve)
    monkeypatch.setattr(adapter, "build_system_prompt", prompt)
    monkeypatch.setattr(assistant, "run", fake_run)
    monkeypatch.setattr(assistant, "resolve_pending_tool_use", fake_resolve)
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter, "_persist_automation_history", lambda *_args: None)

    async def fake_continuation(_session_id, source_factory):
        source = await source_factory(request)
        async for event in source:
            yield event

    monkeypatch.setattr(adapter, "_stream_continuation", fake_continuation)

    if entry_path == "initial":
        await automation_runner(request)
    elif entry_path == "followup":
        async for _event in stream_automation_followup(
            request.session_id, [{"role": "user", "content": "continue"}]
        ):
            pass
    else:
        async for _event in adapter.stream_automation_resolution(
            request.session_id, "tool-1", "approve"
        ):
            pass

    assert resolved == ["configured-model"]
    assert prompts == [{"model": model, "base_url": base_url}]
    assert calls[0][1]["model_context"] is context
    assert calls[0][1]["model"] == "configured-model"


def test_corrupt_automation_transcript_is_quarantined_and_never_overwritten(db_isolated):
    """Sessions are DB-backed now (Wave 5E) — there is no FILE left to quarantine bytes
    from (``assistant.load_session_durable``'s own docstring: "a DB read either succeeds
    or the database itself is broken"). The fail-closed guarantee this pins survives in the
    DB-era shape instead: a stored transcript that is not a JSON array of message objects
    must RAISE rather than being silently overwritten with synthesized run-fact history."""
    from backend.db.automation_store import AutomationStore
    from backend.db.session_store import SessionStore

    assistant.SESSIONS.clear()
    automation = AutomationStore().create(
        {
            "name": "Owned run",
            "instructions": "Review it",
            "trigger": {"kind": "manual"},
        }
    )
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-run-corrupt"}
    )
    # Revision 0051: a transcript is ROWS now, so "stored but malformed" is no longer a
    # representable state — the failure mode this test was written for is designed out. The
    # guarantee survives one step earlier, at the WRITE: a malformed transcript is refused
    # rather than stored (pre-0051 the JSON column took anything and only the next read
    # complained), and an EXISTING transcript is never silently replaced.
    malformed = {"not": "a transcript"}  # wrong shape: a dict, not a list of message objects
    with pytest.raises(DataIntegrityError, match="not a JSON array of message objects"):
        SessionStore().set_transcript("automation-run-corrupt", malformed)

    real = [{"role": "user", "content": "the run already said something"}]
    SessionStore().set_transcript("automation-run-corrupt", real)
    materialize_automation_session(run, automation)

    got = SessionStore().get_transcript("automation-run-corrupt")[0]
    # `ts` is stamped at INSERT since revision 0053, so a round-tripped message carries one
    # the caller never supplied. Compare on what the caller actually wrote.
    assert {k: v for k, v in got.items() if k != "ts"} == real[0], (
        "an existing transcript must never be silently overwritten with synthesized history"
    )


def test_materialization_fails_when_automation_ownership_metadata_cannot_persist(
    db_isolated, monkeypatch
):
    """A session's transcript (SessionStore.set_transcript) and its ownership metadata
    (SessionStore.upsert_meta) are two separate writes even though they land on the same
    `sessions` row (Wave 5E) — if the metadata write fails, materialization must fail loud
    and clean up the freshly-created transcript rather than leaving an unowned session
    behind (was: a failing atomic_write_text on the legacy index.json)."""
    from backend.db.session_store import SessionStore

    assistant.SESSIONS.clear()

    def fail_meta(self, session_id, patch, *, fail_loud=False):
        raise OSError("index disk full")

    monkeypatch.setattr(SessionStore, "upsert_meta", fail_meta)
    run = {
        "id": "run_index_failure",
        "automation_id": "automation_a",
        "session_id": "automation-run-index-failure",
        "status": "running",
        "trigger": {"kind": "manual"},
        "execution": {"kind": "lucius"},
    }

    with pytest.raises(OSError, match="index disk full"):
        materialize_automation_session(
            run,
            {
                "id": "automation_a",
                "name": "Owned Automation",
                "instructions": "Review it",
                "execution": {"kind": "lucius"},
            },
        )

    assert SessionStore().get_transcript("automation-run-index-failure") is None, (
        "the freshly-created transcript must be rolled back when ownership metadata fails"
    )


@pytest.mark.skip(
    reason="Pinned the pre-migration two-step file contract: write index.json, then "
    "READ IT BACK to verify the write actually landed (atomic_write_text could return "
    "successfully without the bytes being durably there). Sessions are one DB row now "
    "(backend/db/session_store.py) and SessionStore.upsert_meta is a single transactional "
    "write with no separate verify step — 'a call that returns normally has already "
    "committed' (assistant.save_session_durable's own docstring). There is no DB-shaped "
    "failure mode left where the write call returns success but the data didn't land: that "
    "scenario is now indistinguishable from — and fully covered by — "
    "test_materialization_fails_when_automation_ownership_metadata_cannot_persist above. "
    "Flagged for owner: delete outright, this is not a seeding fix or a production bug."
)
def test_materialization_verifies_automation_ownership_metadata_after_write(tmp_path, monkeypatch):
    import backend.paths as paths

    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "sessions")
    assistant.SESSIONS.clear()
    real_atomic_write = assistant.atomic_write_text

    def drop_index_write(path, text):
        if path.name != "index.json":
            real_atomic_write(path, text)

    monkeypatch.setattr(assistant, "atomic_write_text", drop_index_write)
    run = {
        "id": "run_index_verification",
        "automation_id": "automation_a",
        "session_id": "automation-run-index-verification",
        "status": "running",
        "trigger": {"kind": "manual"},
        "execution": {"kind": "lucius"},
    }

    with pytest.raises(OSError, match="index verification failed"):
        materialize_automation_session(
            run,
            {
                "id": "automation_a",
                "name": "Owned Automation",
                "instructions": "Review it",
                "execution": {"kind": "lucius"},
            },
        )

    assert not (tmp_path / "sessions" / "automation-run-index-verification.json").exists()


def test_chat_delete_cannot_remove_an_automation_owned_conversation(monkeypatch, db_isolated):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.chat import router as chat_router
    from backend.db.automation_store import AutomationStore
    from backend.db.session_store import SessionStore

    assistant.SESSIONS.clear()
    automation = AutomationStore().create(
        {
            "name": "Owned run",
            "instructions": "Review it",
            "trigger": {"kind": "manual"},
        }
    )
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-run-owned"}
    )
    materialize_automation_session(run, automation)

    app = FastAPI()
    app.include_router(chat_router)
    with TestClient(app) as client:
        response = client.delete(f"/api/chat/{run['session_id']}")

    assert response.status_code == 409
    assert "owned by their Automation" in response.json()["detail"]
    assert SessionStore().get_transcript("automation-run-owned") is not None
    assert AutomationRunStore().find_by_id(run["id"])["session_id"] == run["session_id"]


def test_automation_cleanup_retries_an_already_deleted_owned_session_from_durable_proof(
    tmp_path, monkeypatch
):
    import backend.paths as paths

    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(
        "backend.ai.automation_runner_adapter._delete_pending_for_sessions",
        lambda _session_ids: None,
    )
    monkeypatch.setattr("backend.services.artifacts.delete_for_session", lambda _session_id: 0)
    assistant.SESSIONS.clear()
    session_id = "automation-run-owned-delete"
    assistant.SESSIONS[session_id] = [{"role": "user", "content": "Run it"}]
    assistant.save_session_durable(session_id, assistant.SESSIONS[session_id])
    assistant.upsert_session_meta(session_id, title="Owned", source="automation")
    context = {
        "automation": {
            "id": "automation-a",
            "status": "deleting",
            "deletion": {
                "version": 1,
                "owned_session_ids": [session_id],
                "runs": [{"id": "run-a", "session_id": session_id}],
            },
        },
        "runs": [{"id": "run-a", "session_id": session_id}],
    }

    assert automation_cleanup(context, phase="delete") == {
        "ok": True,
        "owned_session_ids": [session_id],
    }
    assert automation_cleanup(context, phase="delete") == {
        "ok": True,
        "owned_session_ids": [session_id],
    }

    no_durable_proof = {
        "automation": {"id": "automation-a"},
        "runs": [{"id": "run-a", "session_id": session_id}],
    }
    assert automation_cleanup(no_durable_proof, phase="delete")["ok"] is False


def test_automation_cleanup_durable_proof_never_authorizes_a_reowned_session(tmp_path, monkeypatch):
    import backend.paths as paths

    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "sessions")
    assistant.SESSIONS.clear()
    session_id = "automation-run-reowned"
    assistant.upsert_session_meta(session_id, title="Chat", source="chat")
    context = {
        "automation": {
            "id": "automation-a",
            "status": "deleting",
            "deletion": {
                "version": 1,
                "owned_session_ids": [session_id],
                "runs": [{"id": "run-a", "session_id": session_id}],
            },
        },
        "runs": [{"id": "run-a", "session_id": session_id}],
    }

    result = automation_cleanup(context, phase="delete")

    assert result["ok"] is False
    assert result["owned_session_ids"] == []


def test_automation_cleanup_proves_an_unstamped_session_by_its_run_derived_id(
    tmp_path, monkeypatch
):
    """A 2026-09-02 run's index entry lost its ``source`` stamp, and that one row blocked
    deleting its automation forever. ``automation-run-{run_id}`` named by that same run is
    proof on its own; an explicit other source, or an id not derived from the run, is not."""
    import backend.paths as paths

    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "sessions")
    assistant.SESSIONS.clear()
    stamped = "automation-run-run-a"
    unstamped = "automation-run-run-b"
    assistant.upsert_session_meta(stamped, title="Owned", source="automation")
    assistant.upsert_session_meta(unstamped, title="New conversation")
    runs = [
        {"id": "run-a", "session_id": stamped},
        {"id": "run-b", "session_id": unstamped},
    ]

    proof = automation_cleanup({"automation": {"id": "automation-a"}, "runs": runs}, phase="prove")
    assert proof == {"ok": True, "owned_session_ids": [stamped, unstamped]}

    borrowed = [{"id": "run-c", "session_id": unstamped}]
    assert automation_cleanup({"automation": {}, "runs": borrowed}, phase="prove")["ok"] is False

    assistant.upsert_session_meta(unstamped, title="Chat", source="chat")
    assert automation_cleanup({"automation": {}, "runs": runs}, phase="prove")["ok"] is False


@pytest.mark.asyncio
async def test_run_fails_instead_of_completing_when_final_transcript_cannot_persist(
    monkeypatch,
):
    session_id = "automation-run-persist-failure"
    assistant.SESSIONS[session_id] = [{"role": "user", "content": "Run it"}]
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *args, **kwargs: None)

    async def fake_run(*args, **kwargs):
        assistant.SESSIONS[session_id].append({"role": "assistant", "content": "Done"})
        yield {"type": "turn_done", "usage": {"model": "model-a"}}

    monkeypatch.setattr(assistant, "run", fake_run)
    monkeypatch.setattr(
        assistant,
        "save_session_durable",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = await automation_runner(
        AutomationRunnerRequest(
            automation_id="automation_a",
            run_id="run_a",
            session_id=session_id,
            title="Run",
            instructions="Run it",
            model="model-a",
        )
    )

    assert result.status == "failed"
    assert "disk full" in result.error


def test_stale_running_run_fails_and_releases_its_runtime_lock(db_isolated):
    run = AutomationRunStore().start(
        {
            "automation_id": "missing_automation",
            "session_id": "automation-run-stale",
        }
    )
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    AutomationRuntimeStore().put_for_automation("missing_automation", {"running_run_id": run["id"]})

    outcome = recover_automation_runs(materialize_session=lambda row, automation: row["session_id"])

    updated = AutomationRunStore().find_by_id(run["id"])
    assert outcome["recovered"] == 1
    assert updated["status"] == "failed"
    assert "restart" in updated["error"].lower()
    assert AutomationRuntimeStore().get_for_automation("missing_automation") is None


def test_restart_keeps_a_durable_waiting_approval_and_leaves_the_schedule_free(db_isolated):
    """Renamed from "...and_restores_its_lock". The approval surviving a restart is the point
    and is unchanged; re-claiming the execution slot for it was the bug. A waiting run is
    parked on a card, not executing, and holding the slot froze every later scheduled run
    behind it — restoring that on boot would have re-frozen it after every restart.

    PendingPlanStore is DB-backed now (WAVE 5D2b, JSON->DB) — db_isolated already covers
    its isolation, no class-level __init__ override needed."""
    from backend.db.automation_runtime_store import AutomationRuntimeStore
    from backend.db.pending_plan_store import PendingPlanStore

    run = AutomationRunStore().start(
        {
            "automation_id": "automation_waiting",
            "session_id": "automation-run-waiting",
        }
    )
    AutomationRunStore().transition(
        run["id"], {"running"}, "waiting", {"pending_tool_ids": ["tool_1"]}
    )
    PendingPlanStore().stage_pending(
        "tool_1", "create_payees", {"name": "Acme"}, session_id=run["session_id"]
    )

    outcome = recover_automation_runs(
        materialize_session=lambda row, automation: row["session_id"],
        pending_for_session=_pending_ids,
    )

    assert outcome["recovered"] == 0
    assert AutomationRunStore().find_by_id(run["id"])["status"] == "waiting"
    # No execution claim: the approval is durable, the schedule behind it is not blocked.
    assert AutomationRuntimeStore().get_for_automation("automation_waiting") is None


def test_waiting_resolution_releases_lock_and_followups_do_not_double_count(db_isolated):
    from backend.db.automation_runtime_store import AutomationRuntimeStore
    from backend.db.automation_store import AutomationStore

    automation = AutomationStore().create(
        {
            "name": "Review",
            "instructions": "Review it",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
        }
    )
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-run-review"}
    )
    AutomationRunStore().transition(
        run["id"], {"running"}, "waiting", {"pending_tool_ids": ["tool_1"]}
    )
    AutomationRuntimeStore().put_for_automation(automation["id"], {"running_run_id": run["id"]})

    current_automation, baseline = begin_automation_continuation(run["session_id"])
    waiting = reconcile_automation_continuation(
        current_automation,
        baseline,
        AutomationRunnerResult(
            status="waiting", session_id=run["session_id"], pending_tool_ids=["tool_2"]
        ),
    )
    assert waiting["status"] == "waiting"
    # Parking on the approval releases the execution slot — the continuation below re-acquires
    # it, which is what keeps "one executing run per Automation" true without the parked run
    # blocking the schedule in the meantime.
    assert AutomationRuntimeStore().get_for_automation(automation["id"]) is None

    current_automation, baseline = begin_automation_continuation(run["session_id"])
    completed = reconcile_automation_continuation(
        current_automation,
        baseline,
        AutomationRunnerResult(
            status="completed",
            session_id=run["session_id"],
            summary="Done",
            files=[{"name": "report.csv", "kind": "csv"}],
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.01,
        ),
    )
    assert completed["status"] == "completed"
    assert completed["files"] == [{"name": "report.csv", "kind": "csv"}]
    assert AutomationRuntimeStore().get_for_automation(automation["id"]) is None
    assert AutomationStore().find_by_id(automation["id"])["run_count"] == 1

    current_automation, baseline = begin_automation_continuation(run["session_id"])
    reconcile_automation_continuation(
        current_automation,
        baseline,
        AutomationRunnerResult(
            status="completed",
            session_id=run["session_id"],
            summary="Follow-up",
            input_tokens=2,
            output_tokens=1,
            cost_usd=0.002,
        ),
    )
    final = AutomationRunStore().find_by_id(run["id"])
    assert final["input_tokens"] == 12
    assert final["cost_usd"] == 0.012
    assert AutomationStore().find_by_id(automation["id"])["run_count"] == 1


async def _drain_one_then_wait(entered):
    from backend.core.automation_runner import get_automation_approval

    assert get_automation_approval() is not None
    yield {"type": "text_delta", "text": "partial"}
    await entered.wait()


@pytest.mark.asyncio
async def test_disconnected_followup_fails_safely_releases_lock_and_resets_context(
    db_isolated, tmp_path, monkeypatch
):
    import backend.paths as paths

    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "sessions")

    import asyncio

    from backend.core.audit_context import get_audit_scope
    from backend.core.automation_runner import get_automation_approval
    from backend.core.cost_context import get_cost_surface
    from backend.db.automation_runtime_store import AutomationRuntimeStore
    from backend.db.automation_store import AutomationStore

    automation = AutomationStore().create(
        {
            "name": "Review",
            "instructions": "Review it",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
        }
    )
    run = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": "automation-run-disconnect",
            "stats": {"lifecycle_finalized": True},
        }
    )
    materialize_automation_session(run, automation)
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})
    never = asyncio.Event()

    monkeypatch.setattr(
        assistant,
        "run",
        lambda *args, **kwargs: _drain_one_then_wait(never),
    )

    stream = stream_automation_followup(
        run["session_id"], [{"role": "user", "content": "continue"}]
    )
    assert await stream.__anext__() == {"type": "text_delta", "text": "partial"}
    await stream.aclose()

    updated = AutomationRunStore().find_by_id(run["id"])
    assert updated["status"] == "failed"
    assert "before Lucius completed" in updated["error"]
    assert AutomationRuntimeStore().get_for_automation(automation["id"]) is None
    assert get_automation_approval() is None
    assert get_audit_scope() is None
    assert get_cost_surface() is None


async def test_stream_automation_followup_honors_an_explicit_model_override(
    tmp_path, monkeypatch, db_isolated
):
    """A message typed into an Automation-owned conversation used to always run on the
    Automation's OWN configured model — `stream_automation_followup` re-derived it from
    `request_for_run` regardless of what api/chat.py's caller had already resolved (the
    user's composer choice, or a stored per-conversation override), silently discarding it.
    """
    import backend.paths as paths

    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "sessions")

    from backend.db.automation_store import AutomationStore

    automation = AutomationStore().create(
        {
            "name": "Log trades",
            "instructions": "Log it",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
            "model": "codex_cli:gpt-5.6-luna",
        }
    )
    run = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": "automation-run-model-override",
            "stats": {"lifecycle_finalized": True},
        }
    )
    materialize_automation_session(run, automation)
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})

    seen_models: list[str | None] = []

    async def fake_run(*args, **kwargs):
        seen_models.append(kwargs.get("model"))
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)

    stream = stream_automation_followup(
        run["session_id"], [{"role": "user", "content": "which ones?"}], model="haiku"
    )
    async for _ in stream:
        pass

    assert seen_models == ["haiku"], (
        "the explicit override must win over the Automation's own model"
    )


async def test_stream_automation_followup_falls_back_to_the_automations_own_model(
    tmp_path, monkeypatch, db_isolated
):
    """No explicit override (a normal follow-up with nothing picked) — the Automation's
    configured model is still the right fallback, unchanged from before."""
    import backend.paths as paths

    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "sessions")

    from backend.db.automation_store import AutomationStore

    automation = AutomationStore().create(
        {
            "name": "Log trades",
            "instructions": "Log it",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
            "model": "codex_cli:gpt-5.6-luna",
        }
    )
    run = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": "automation-run-model-fallback",
            "stats": {"lifecycle_finalized": True},
        }
    )
    materialize_automation_session(run, automation)
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})

    seen_models: list[str | None] = []

    async def fake_run(*args, **kwargs):
        seen_models.append(kwargs.get("model"))
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)

    stream = stream_automation_followup(
        run["session_id"], [{"role": "user", "content": "which ones?"}]
    )
    async for _ in stream:
        pass

    assert seen_models == ["codex_cli:gpt-5.6-luna"]


@pytest.mark.asyncio
async def test_followup_setup_failure_reconciles_and_releases_the_claim(
    db_isolated, tmp_path, monkeypatch
):
    import backend.paths as paths
    from backend.db.automation_runtime_store import AutomationRuntimeStore
    from backend.db.automation_store import AutomationStore

    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "sessions")

    automation = AutomationStore().create(
        {
            "name": "Review",
            "instructions": "Review it",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
        }
    )
    run = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": "automation-run-setup-failure",
            "stats": {"lifecycle_finalized": True},
        }
    )
    materialize_automation_session(run, automation)
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})
    monkeypatch.setattr(
        assistant,
        "load_session_durable",
        lambda _session_id: (_ for _ in ()).throw(OSError("transcript read failed")),
    )

    stream = stream_automation_followup(
        run["session_id"], [{"role": "user", "content": "continue"}]
    )
    with pytest.raises(OSError, match="transcript read failed"):
        await stream.__anext__()

    updated = AutomationRunStore().find_by_id(run["id"])
    assert updated["status"] == "failed"
    assert updated["error"] == "transcript read failed"
    assert AutomationRuntimeStore().get_for_automation(automation["id"]) is None


@pytest.mark.asyncio
async def test_automation_followup_replays_two_residue_skills_with_distinct_arguments(
    tmp_path, monkeypatch, db_isolated
):
    import backend.db.skill_store as skill_store_mod
    from backend.db.automation_store import AutomationStore

    # Sessions are DB-backed now (Wave 5E) — no more SESSIONS_DIR/assistant._sessions_dir
    # to point at tmp_path; db_isolated already isolates the `sessions` table.
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store_mod, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store_mod, "SKILLS_STATE_JSON", tmp_path / "skills-state.json")
    monkeypatch.setattr(assistant, "_record_cost", lambda *args, **kwargs: None)
    assistant.SESSIONS.clear()

    automation = AutomationStore().create(
        {
            "name": "Residue review",
            "instructions": "Review it",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
        }
    )
    run = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": "automation-residue-skills",
            "stats": {"lifecycle_finalized": True},
        }
    )
    materialize_automation_session(run, automation)
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})

    SkillStore().create({"name": "automation-first", "body": "Automation first uses $ARGUMENTS."})
    SkillStore().create({"name": "automation-second", "body": "Automation second uses $ARGUMENTS."})
    recovered = [
        pending_injection.QueuedMessage(
            client_id="auto-first",
            text="/automation-first alpha",
            direct_invocations=(
                pending_injection.QueuedSkillInvocation("automation-first", "alpha", "user"),
            ),
        ),
        pending_injection.QueuedMessage(
            client_id="auto-second",
            text="/automation-second beta",
            direct_invocations=(
                pending_injection.QueuedSkillInvocation("automation-second", "beta", "user"),
            ),
        ),
    ]

    class _Provider:
        def __init__(self):
            self.messages = None

        async def stream_chat(self, messages, tools, system, model, effort="medium"):
            self.messages = messages
            yield {"type": "text_delta", "text": "done"}
            yield {"type": "done", "stop_reason": "end_turn"}

    provider = _Provider()
    context = assistant.ModelTurnContext(
        model="automation-test-model",
        provider_key="",
        transport="anthropic",
        base_url="",
        provider=provider,
    )
    real_run = assistant.run

    def run_with_context(*args, **kwargs):
        kwargs["model_context"] = context
        return real_run(*args, **kwargs)

    monkeypatch.setattr(assistant, "run", run_with_context)
    adopted = []
    stream = stream_automation_followup(
        run["session_id"],
        [{"role": "user", "content": "continue"}],
        recovered_injections=recovered,
        on_recovered_adopted=lambda items: adopted.extend(items),
    )
    events = [event async for event in stream]

    assert [item is original for item, original in zip(adopted, recovered)] == [True, True]
    payload = json.dumps(provider.messages)
    assert payload.index("Automation first uses alpha.") < payload.index(
        "Automation second uses beta."
    )
    assert "Automation first uses beta." not in payload
    assert "Automation second uses alpha." not in payload
    assert [event["message_id"] for event in events if event["type"] == "message_delivered"] == [
        "auto-first",
        "auto-second",
    ]
    stored = assistant.load_session_durable(run["session_id"]) or []
    assert len([entry for entry in stored if entry.get("_onw_skill")]) == 2


def test_restart_finishes_terminal_lifecycle_once_after_run_marker_crash(
    db_isolated, tmp_path, monkeypatch
):
    from datetime import datetime

    from backend.db.automation_runtime_store import AutomationRuntimeStore
    from backend.db.automation_store import AutomationStore

    scheduled_for = "2026-08-02T08:00:00+00:00"
    automation = AutomationStore().create(
        {
            "name": "Hourly review",
            "instructions": "Review it",
            "trigger": {"kind": "schedule", "type": "interval", "seconds": 3600},
            "execution": {"kind": "lucius"},
            "next_run_at": scheduled_for,
        }
    )
    run = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": "automation-run-crash-window",
            "trigger": {"kind": "schedule", "scheduled_for": scheduled_for},
            "context": {
                "trigger": {"kind": "schedule", "scheduled_for": scheduled_for},
                "payload": {"scheduled_for": scheduled_for},
            },
        }
    )
    terminal, _ = AutomationRunStore().transition(run["id"], {"running"}, "completed", {})
    AutomationRuntimeStore().put_for_automation(automation["id"], {"running_run_id": run["id"]})
    original_patch = AutomationRunStore.patch
    injected = False

    def crash_before_run_marker(self, run_id, expected, patch):
        nonlocal injected
        if not injected and (patch.get("stats") or {}).get("lifecycle_finalized"):
            injected = True
            raise OSError("injected crash before run marker")
        return original_patch(self, run_id, expected, patch)

    monkeypatch.setattr(AutomationRunStore, "patch", crash_before_run_marker)
    with pytest.raises(OSError, match="injected crash"):
        recover_automation_runs(materialize_session=lambda row, automation: row["session_id"])

    after_crash = AutomationStore().find_by_id(automation["id"])
    assert after_crash["run_count"] == 1
    assert after_crash["last_status"] == "completed"
    assert after_crash["last_run_at"] == terminal["finished_at"]
    # This previously pinned "2026-08-02T09:00:00+00:00" — one interval past a scheduled_for
    # that is hardcoded in the past, so the pinned value was itself already stale by the time
    # the test ran. That is precisely the catch-up bug (see compute_next_run), so the literal
    # was encoding it. What this test is actually about is finalizing the lifecycle exactly
    # once; for the schedule, assert the property that matters instead of a frozen literal.
    next_run_at = datetime.fromisoformat(after_crash["next_run_at"])
    assert next_run_at > datetime.fromisoformat(terminal["finished_at"])
    # Still on the original :00 grid — catching up must not re-phase the cadence.
    assert (next_run_at.minute, next_run_at.second) == (0, 0)

    monkeypatch.setattr(AutomationRunStore, "patch", original_patch)
    recover_automation_runs(materialize_session=lambda row, automation: row["session_id"])

    finalized = AutomationRunStore().find_by_id(run["id"])
    assert finalized["stats"]["lifecycle_finalized"] is True
    assert AutomationStore().find_by_id(automation["id"])["run_count"] == 1
    assert AutomationRuntimeStore().get_for_automation(automation["id"]) is None


@pytest.mark.asyncio
async def test_terminal_run_keeps_ownership_until_lifecycle_finalization_succeeds(
    db_isolated, monkeypatch
):
    import backend.services.automations.runs as runs
    from backend.db.automation_runtime_store import AutomationRuntimeStore
    from backend.db.automation_store import AutomationStore

    scheduled_for = "2026-08-02T08:00:00+00:00"
    automation = AutomationStore().create(
        {
            "name": "Hourly review",
            "instructions": "Review it",
            "trigger": {"kind": "schedule", "type": "interval", "seconds": 3600},
            "execution": {"kind": "lucius"},
            "next_run_at": scheduled_for,
        }
    )
    run = AutomationRunStore().start(
        {
            "id": "run_first",
            "automation_id": automation["id"],
            "session_id": "automation-run-first",
            "trigger": {"kind": "schedule", "scheduled_for": scheduled_for},
            "context": {"scheduled_for": scheduled_for},
        }
    )
    assert AutomationRuntimeStore().claim_run(automation["id"], run["id"])

    async def complete(*args, **kwargs):
        return AutomationRunnerResult(
            status="completed",
            session_id=run["session_id"],
            summary="Done",
        )

    monkeypatch.setattr(runs, "run_with_automation_runner", complete)
    monkeypatch.setattr(
        runs,
        "_finish_automation_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("finalization disk full")),
    )

    with pytest.raises(OSError, match="finalization disk full"):
        await runs._execute_run(automation, run, {"scheduled_for": scheduled_for})

    assert (
        AutomationRuntimeStore().get_for_automation(automation["id"])["running_run_id"] == run["id"]
    )
    assert runs._claim_runtime(automation["id"], "run_second") == (False, False)


def test_continuation_claims_before_transition_blocks_an_interleaved_new_run(
    db_isolated, monkeypatch
):
    from backend.db.automation_runtime_store import AutomationRuntimeStore
    from backend.db.automation_store import AutomationStore

    automation = AutomationStore().create(
        {
            "name": "Review",
            "instructions": "Review it",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
        }
    )
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-run-interleave"}
    )
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})
    original_transition = AutomationRunStore.transition
    interloper_claims = []

    def transition_after_interloper(self, run_id, expected, status, patch):
        interloper_claims.append(AutomationRuntimeStore().claim_run(automation["id"], "run_newer"))
        return original_transition(self, run_id, expected, status, patch)

    monkeypatch.setattr(AutomationRunStore, "transition", transition_after_interloper)
    _automation, started = begin_automation_continuation(run["session_id"])

    assert interloper_claims == [False]
    assert started["status"] == "running"
    assert (
        AutomationRuntimeStore().get_for_automation(automation["id"])["running_run_id"] == run["id"]
    )


def test_continuation_rolls_back_new_claim_when_transition_loses_the_race(db_isolated, monkeypatch):
    from backend.core.errors import ConflictError
    from backend.db.automation_run_store import TransitionConflict
    from backend.db.automation_runtime_store import AutomationRuntimeStore
    from backend.db.automation_store import AutomationStore

    automation = AutomationStore().create(
        {
            "name": "Review",
            "instructions": "Review it",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
        }
    )
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-run-race"}
    )
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})

    def lose_transition(self, run_id, expected, status, patch):
        raise TransitionConflict(run_id)

    monkeypatch.setattr(AutomationRunStore, "transition", lose_transition)
    with pytest.raises(ConflictError, match="already processing"):
        begin_automation_continuation(run["session_id"])

    assert AutomationRuntimeStore().get_for_automation(automation["id"]) is None


def test_continuation_reclaims_an_owner_whose_run_row_is_missing(db_isolated):
    from backend.db.automation_runtime_store import AutomationRuntimeStore
    from backend.db.automation_store import AutomationStore

    automation = AutomationStore().create(
        {
            "name": "Review",
            "instructions": "Review it",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
        }
    )
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-run-orphan"}
    )
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})
    AutomationRuntimeStore().put_for_automation(
        automation["id"],
        {"running_run_id": "run_missing", "session_allowed_tools": {run["session_id"]: []}},
    )

    _automation, started = begin_automation_continuation(run["session_id"])

    assert started["status"] == "running"
    assert AutomationRuntimeStore().get_for_automation(automation["id"]) == {
        "running_run_id": run["id"],
        "session_allowed_tools": {run["session_id"]: []},
    }


def test_startup_recovery_sweeps_orphan_run_owners_without_dropping_runtime_state(db_isolated):
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    AutomationRuntimeStore().put_for_automation(
        "automation_missing",
        {"running_run_id": "run_missing", "queue": [{"event_id": "event_1"}]},
    )

    recover_automation_runs(materialize_session=lambda row, automation: row["session_id"])

    assert AutomationRuntimeStore().get_for_automation("automation_missing") == {
        "queue": [{"event_id": "event_1"}]
    }


def test_a_continuation_can_persist_progress_after_a_tool_event():
    """Regression — the approve/deny and continuation flows were both dead.

    ``_EventProjection.patch()`` referenced ``self.reported_steps``, a property deleted in an
    earlier refactor; the one call site was missed. ``patch()`` runs whenever
    ``should_persist()`` is true, which every ``tool_use_start``/``tool_done`` makes true —
    and a ``tool_done`` fires on EVERY approve or deny of a paused Automation step. So
    tapping Approve on a run card raised AttributeError inside the stream, which the
    ``except BaseException`` re-raised, failing the run.

    No existing test reached ``patch()`` mid-continuation: the continuation tests all bail
    out before an event ever reaches ``consume()``.
    """
    from backend.ai.automation_runner_adapter import _EventProjection

    projection = _EventProjection(
        AutomationRunnerRequest(
            automation_id="automation_a",
            run_id="run_a",
            session_id="automation-run-a",
            title="Run",
            instructions="Run it",
        )
    )
    event = {"type": "tool_use_start", "id": "toolu_1", "name": "telegram_send_message"}

    assert projection.should_persist(event) is True
    projection.consume(event)

    patch = projection.patch()  # used to raise AttributeError

    assert patch["steps"][0]["tool_use_id"] == "toolu_1"
    assert patch["steps"][0]["status"] == "running"


def test_finishing_a_run_that_was_blocked_for_days_does_not_leave_a_past_next_run(
    tmp_path, monkeypatch, db_isolated
):
    """End-to-end at the real finalize path, not just the schedule math.

    Repro: "Daily Trades Summary" sat on a notify approval card for five days. Finalizing
    it wrote a next_run_at four days in the PAST — which the scheduler reads as due on the
    very next tick. It fires, finalizes, advances one more day, and repeats: five paid runs
    back to back, each summarizing a different stale date, each sending a Telegram message
    nobody needed by then.

    The assertion that matters is not which slot it picks but that the slot is in the
    FUTURE. A next_run_at at or before now is, by construction, an immediate replay.
    """
    from datetime import datetime, timedelta, timezone

    from backend.db.automation_store import AutomationStore
    from backend.services.automations import runs as run_mod

    scheduled_for = "2025-02-04T09:00:00+00:00"
    blocked_days = 5
    finished_at = "2025-02-09T14:30:00+00:00"
    # The reschedule floors against the clock seam, so pin it — otherwise this asserts
    # against real wall-clock and goes green or red depending on the hour it runs.
    monkeypatch.setattr(
        "backend.services.automations.service.now_utc",
        lambda: datetime.fromisoformat(finished_at),
    )

    automation = AutomationStore().create(
        {
            "name": "Daily Trades Summary",
            "instructions": "Summarize IBKR trades",
            "trigger": {"kind": "schedule", "type": "daily", "time": "09:00", "tzid": "UTC"},
            "execution": {"kind": "lucius"},
            "next_run_at": scheduled_for,
        }
    )
    run = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": "automation-run-blocked-approval",
            "trigger": {"kind": "schedule", "scheduled_for": scheduled_for},
        }
    )
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})
    finished = AutomationRunStore().find_by_id(run["id"])
    finished["finished_at"] = finished_at

    run_mod._finish_automation_once(
        automation, finished, "completed", {"scheduled_for": scheduled_for}
    )

    next_run_at = datetime.fromisoformat(
        AutomationStore().find_by_id(automation["id"])["next_run_at"]
    )
    resolved = datetime.fromisoformat(finished_at)
    assert next_run_at > resolved, (
        f"next_run_at {next_run_at.isoformat()} is not after the moment the run finished — "
        "the scheduler will fire it immediately and replay the backlog"
    )
    # And it resumes at the real next slot, not somewhere in the middle of the missed days.
    assert next_run_at == datetime(2025, 2, 10, 9, 0, tzinfo=timezone.utc)
    assert next_run_at - resolved < timedelta(days=blocked_days)


@pytest.mark.parametrize(
    ("trigger", "expected"),
    [
        (
            {"kind": "schedule", "type": "monthly", "day": 28, "time": "09:00"},
            "2026-10-28T09:00:00+07:00",
        ),
        (
            {"kind": "schedule", "type": "daily", "time": "09:00"},
            "2026-09-29T09:00:00+07:00",
        ),
    ],
)
def test_a_machine_timezone_change_does_not_rerun_the_slot_that_just_ran(
    trigger, expected, monkeypatch, db_isolated
):
    """Live incident, 2026-09-28: "Monthly Kestrel Dash Salary" (tz-less, 09:00 on the 28th)
    ran at 09:00+08:00, and the machine had since moved from Singapore to Ho Chi Minh (+07).
    Rescheduling looked for the next 09:00 on the 28th on the NEW clock, found 09:00+07:00 —
    one hour later, still today — and ran the same occurrence a second time. It had already
    happened to a weekly and a daily job the week before.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import backend.services.automations.schedules as schedules
    from backend.db.automation_store import AutomationStore
    from backend.services.automations import runs as run_mod

    scheduled_for = "2026-09-28T09:00:00+08:00"
    monkeypatch.setattr(schedules, "_local_tz", lambda: ZoneInfo("Asia/Ho_Chi_Minh"))
    monkeypatch.setattr(
        "backend.services.automations.service.now_utc",
        lambda: datetime.fromisoformat("2026-09-28T01:01:15+00:00"),
    )
    automation = AutomationStore().create(
        {
            "name": "Monthly salary",
            "instructions": "Record the salary",
            "trigger": trigger,
            "execution": {"kind": "lucius"},
            "next_run_at": scheduled_for,
        }
    )
    run = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": "automation-run-tz-move",
            "trigger": {"kind": "schedule", "scheduled_for": scheduled_for},
        }
    )
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})
    finished = AutomationRunStore().find_by_id(run["id"])

    run_mod._finish_automation_once(
        automation, finished, "completed", {"scheduled_for": scheduled_for}
    )

    assert AutomationStore().find_by_id(automation["id"])["next_run_at"] == expected


def test_a_plan_included_cost_reaches_the_run_stamped_as_such():
    """The CLI's total_cost_usd is a LIST PRICE when it runs on a Pro/Max login — usage the
    plan already covers, which Anthropic's docs say "isn't relevant for billing purposes".
    claude_cli_provider stamps that as cost_basis="plan_included" precisely so a reader can
    tell an estimate from an invoice.

    The stamp was dropped between the turn and the run: both `turn_done` payloads carried
    cost_usd and model but not cost_basis, so a run stored $10.52 with nothing saying it was
    notional, and the UI showed it as money.
    """
    from backend.ai.automation_runner_adapter import _usage_fields

    fields = _usage_fields(
        {
            "model": "sonnet",
            "input_tokens": 42,
            "output_tokens": 6120,
            "cache_creation_tokens": 91000,
            "cache_read_tokens": 1420000,
            "cost_usd": 10.52,
            "cost_basis": "plan_included",
        }
    )
    assert fields["cost_usd"] == 10.52
    assert fields["stats"]["cost_basis"] == "plan_included"
    # The cache figures are the whole cost story — 42 in / 6120 out cannot explain $10.52.
    assert fields["cache_read_tokens"] == 1420000
    assert fields["cache_creation_tokens"] == 91000


def test_the_run_message_states_todays_date():
    """A run reasoning about "the last day" has to know what day it is.

    The clock DOES exist in the shared prompt, but it rides the per-turn tail, which is
    attached once to the last user message at turn start. An Automation then runs 20+ tool
    rounds with context folding active, so the date scrolls out of reach and can be
    summarised away — a run once said "I don't know today's exact date. The turn
    context doesn't state it," then guessed a window starting in the FUTURE.

    It cannot move to the cached prefix (tests/test_prompt_clock.py forbids a
    minute-resolution value there — it would rewrite the cache every turn). The run's own
    opening message is re-read from history on every round, so it survives the whole run.
    """
    import re

    from backend.ai.automation_runner_adapter import _automation_message
    from backend.core.automation_runner import AutomationRunnerRequest

    request = AutomationRunnerRequest(
        automation_id="a1",
        run_id="r1",
        session_id="s1",
        title="Log mail",
        instructions="Scan the last day of mail.",
        trigger={"kind": "schedule"},
        execution={"kind": "lucius"},
        model="",
        effort="medium",
        payload={},
        permission_mode="auto",
        always_allowed=[],
    )
    context_block = _automation_message(request)["content"][1]["text"]
    assert "TODAY'S DATE:" in context_block
    assert re.search(r"\d{4}-\d{2}-\d{2}", context_block), "needs an ISO date to compute from"
    assert re.search(r"\d{2}:\d{2}", context_block), "and the time, for 'overdue'/'by tonight'"


async def test_a_time_gap_between_materialize_and_execute_does_not_duplicate_the_opening_message(
    db_isolated, monkeypatch
):
    """materialize_automation_session() seeds the session's opening message when a run is
    queued; automation_runner()'s dedup guard used to re-check that by rebuilding the SAME
    message and comparing it byte-for-byte to what got persisted. _automation_message embeds
    _today_line() — minute-resolution — so any real gap between queueing and execution
    starting (scheduling/worker pickup, both routine) changed the timestamp and made the
    equality fail, appending a second, ~full-length copy of the instruction sheet as a real,
    visible message (reproduced against live data: a 9,855-char duplicate). The guard must
    key on presence, not exact content."""
    from backend.db.automation_store import AutomationStore

    assistant.SESSIONS.clear()
    automation = AutomationStore().create(
        {
            "name": "Log mail",
            "instructions": "Scan the last day of mail.",
            "trigger": {"kind": "schedule"},
        }
    )
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-time-gap"}
    )
    request = AutomationRunnerRequest(
        automation_id=automation["id"],
        run_id=run["id"],
        session_id="automation-time-gap",
        title="Log mail",
        instructions="Scan the last day of mail.",
        trigger={"kind": "schedule"},
    )
    materialize_automation_session(run, automation)

    # Simulate a minute passing between materialize (above) and execution (below): the SAME
    # call now returns a different clock string, exactly like a real scheduling delay would.
    monkeypatch.setattr(
        adapter, "_today_line", lambda: "2026-01-01 — Thursday 00:01 UTC (UTC+0000)"
    )

    captured_messages = []

    async def fake_run(_session_id, messages, *_args, **_kwargs):
        captured_messages.append(messages)
        yield {"type": "turn_done", "usage": {}}

    context = assistant.ModelTurnContext(
        model="small-model",
        provider_key="provider-a",
        transport="openai_compatible",
        base_url="https://small.invalid",
        provider=object(),
    )
    monkeypatch.setattr(assistant, "resolve_model_context", lambda *_a, **_k: context)
    monkeypatch.setattr(adapter, "build_system_prompt", lambda **_k: "prompt")
    monkeypatch.setattr(assistant, "run", fake_run)
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *_a, **_k: None)
    monkeypatch.setattr(adapter, "_persist_automation_history", lambda *_a: None)

    await automation_runner(request)

    assert captured_messages == [[]], (
        "the opening message was already materialized — execution must not re-append it "
        "just because a freshly-rebuilt copy no longer matches byte-for-byte"
    )


_LEAKY_ROUNDS = [
    {"type": "text_delta", "text": '<invoke name="list_accounts">\n</invoke>\n'},
    {"type": "text_delta", "text": "Checked your accounts."},
    {"type": "tool_executing", "id": "t1", "name": "list_accounts", "input": {}},
    {"type": "text_delta", "text": "Found 3 unpaid bills totalling SGD 412."},
    {"type": "tool_executing", "id": "t2", "name": "notify", "input": {}},
    {"type": "text_delta", "text": 'Sent the reminder.\n<invoke name="notify">\n</invoke>'},
]


def _assert_clean_summary(summary: str) -> None:
    assert "<invoke" not in summary
    for line in ("Checked your accounts.", "Found 3 unpaid bills", "Sent the reminder."):
        assert line in summary


@pytest.mark.asyncio
async def test_run_summary_never_carries_leaked_tool_call_markup(monkeypatch):
    """Same leak the iOS capture showed live 2026-09-29: the summary was the raw join of
    every round's text_delta, so a round that wrote a call as markup put it on the run card."""
    session_id = "automation-run-leak"
    assistant.SESSIONS[session_id] = [{"role": "user", "content": "Run it"}]
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *args, **kwargs: None)
    monkeypatch.setattr(assistant, "save_session_durable", lambda *args, **kwargs: None)

    async def fake_run(*args, **kwargs):
        for event in _LEAKY_ROUNDS:
            yield event
        yield {"type": "turn_done", "usage": {"model": "model-a"}}

    monkeypatch.setattr(assistant, "run", fake_run)
    result = await automation_runner(
        AutomationRunnerRequest(
            automation_id="automation_a",
            run_id="run_leak",
            session_id=session_id,
            title="Run",
            instructions="Run it",
            model="model-a",
        )
    )

    _assert_clean_summary(result.summary)


def test_continuation_summary_never_carries_leaked_tool_call_markup():
    projection = adapter._EventProjection(
        AutomationRunnerRequest(
            automation_id="automation_a",
            run_id="run_leak",
            session_id="automation-run-leak",
            title="Run",
            instructions="Run it",
            model="model-a",
        )
    )
    for event in _LEAKY_ROUNDS:
        projection.consume(event)

    _assert_clean_summary(projection.summary)
