"""Contract tests for the human-only Open Net Worth Builder workspace."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def test_workspace_root_is_server_controlled(tmp_path, monkeypatch):
    from backend.services import builder

    workspace = tmp_path / "open-net-worth"
    workspace.mkdir()
    monkeypatch.setenv("ONW_BUILDER_ROOT", str(workspace))

    assert builder.workspace_root() == workspace.resolve()


def test_workspace_root_defaults_to_repository(monkeypatch):
    from backend.services import builder

    monkeypatch.delenv("ONW_BUILDER_ROOT", raising=False)

    root = builder.workspace_root()
    assert (root / "backend").is_dir()
    assert (root / "frontend").is_dir()
    assert (root / "package.json").is_file()


@pytest.mark.parametrize(
    ("engine", "mode", "required"),
    [
        ("codex", "plan", ("exec", "--json", "--sandbox", "read-only", "--ephemeral", "-")),
        (
            "codex",
            "build",
            ("exec", "--json", "--sandbox", "workspace-write", "--ephemeral", "-"),
        ),
        (
            "claude",
            "plan",
            ("-p", "--output-format", "stream-json", "--permission-mode", "plan"),
        ),
        (
            "claude",
            "build",
            ("-p", "--output-format", "stream-json", "--permission-mode", "acceptEdits"),
        ),
    ],
)
def test_build_argv_pins_authority(engine, mode, required):
    from backend.services.builder import build_argv

    argv = build_argv(engine, mode, executable=f"/usr/local/bin/{engine}")
    rendered = " ".join(argv)

    for token in required:
        assert token in argv
    assert argv[0] == f"/usr/local/bin/{engine}"
    assert "danger-full-access" not in rendered
    assert "dangerously-bypass" not in rendered
    assert "dangerously-skip-permissions" not in rendered


def test_codex_argv_disables_host_mcp_and_web_tools():
    from backend.services.builder import build_argv

    argv = build_argv("codex", "build", executable="codex")
    rendered = " ".join(argv)

    assert 'approval_policy="never"' in rendered
    assert "mcp_servers={}" in rendered
    assert "tools.web_search=false" in rendered


def test_claude_argv_uses_strict_empty_mcp_config():
    from backend.services.builder import build_argv

    argv = build_argv("claude", "build", executable="claude")

    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--mcp-config") + 1] == "{}"
    assert "--no-session-persistence" in argv


def test_child_env_removes_app_and_provider_secrets(monkeypatch):
    from backend.services.builder import child_env

    monkeypatch.setenv("ONW_SECRET_TOKEN", "secret")
    monkeypatch.setenv("DATA_DIR", "/private/book")
    monkeypatch.setenv("CACHE_DIR", "/private/cache")
    monkeypatch.setenv("LOGS_DIR", "/private/logs")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "slack-secret")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:password@example.test/book")
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    monkeypatch.setenv("SAFE_VISIBLE_VALUE", "not-needed-by-the-cli")

    env = child_env()

    assert env["PATH"] == "/usr/local/bin:/usr/bin"
    assert env["CI"] == "1"
    for key in (
        "ONW_SECRET_TOKEN",
        "DATA_DIR",
        "CACHE_DIR",
        "LOGS_DIR",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "SLACK_BOT_TOKEN",
        "DATABASE_URL",
        "SAFE_VISIBLE_VALUE",
    ):
        assert key not in env


def test_builder_prompt_makes_mode_and_no_git_authority_explicit():
    from backend.services.builder import build_prompt

    plan = build_prompt("plan", "Add a cashflow card")
    build = build_prompt("build", "Add a cashflow card")

    assert "Do not modify files" in plan
    assert "inspect and edit" in build
    assert "Do not commit, push, open a pull request, or deploy" in build
    assert plan.endswith("Add a cashflow card")
    assert build.endswith("Add a cashflow card")


def test_normalize_codex_assistant_and_command_events():
    from backend.services.builder import normalize_event

    assistant = normalize_event(
        "codex",
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}},
    )
    command = normalize_event(
        "codex",
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "npm test",
                "aggregated_output": "12 tests passed",
            },
        },
    )

    assert assistant == [("assistant", "Done.")]
    assert command == [("tool", "npm test"), ("output", "12 tests passed")]


def test_normalize_claude_text_tool_and_result_events():
    from backend.services.builder import normalize_event

    assistant = normalize_event(
        "claude",
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "I will update the page."},
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": "x.tsx"}},
                ]
            },
        },
    )
    result = normalize_event("claude", {"type": "result", "result": "Finished."})

    assert assistant == [
        ("assistant", "I will update the page."),
        ("tool", "Edit • x.tsx"),
    ]
    assert result == [("assistant", "Finished.")]


def test_engine_status_reports_missing_and_installed_commands(monkeypatch):
    from backend.services import builder

    def fake_resolve(engine: str) -> str:
        return "codex" if engine == "codex" else "claude"

    def fake_which(command: str):
        return "/opt/bin/codex" if command == "codex" else None

    def fake_run(argv, **_kwargs):
        assert argv == ["/opt/bin/codex", "--version"]
        return SimpleNamespace(stdout="codex-cli 0.145.0\n", stderr="", returncode=0)

    monkeypatch.setattr(builder, "resolve_engine_command", fake_resolve)
    monkeypatch.setattr(builder.shutil, "which", fake_which)
    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    statuses = {row["id"]: row for row in builder.engine_statuses()}

    assert statuses["codex"]["installed"] is True
    assert statuses["codex"]["version"] == "codex-cli 0.145.0"
    assert statuses["codex"]["build_is_sandboxed"] is True
    assert statuses["claude"]["installed"] is False
    assert "Install" in statuses["claude"]["guidance"]


def test_workspace_changes_are_bounded_and_honest(tmp_path, monkeypatch):
    from backend.services import builder

    monkeypatch.setattr(
        builder.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=" M frontend/src/App.tsx\n?? backend/api/builder.py\n",
            stderr="",
            returncode=0,
        ),
    )

    assert builder.workspace_changes(tmp_path) == [
        "M frontend/src/App.tsx",
        "?? backend/api/builder.py",
    ]


@pytest.mark.asyncio
async def test_manager_runs_executor_and_returns_incremental_events(tmp_path, monkeypatch):
    from backend.services import builder

    workspace = tmp_path / "open-net-worth"
    (workspace / "backend").mkdir(parents=True)
    (workspace / "frontend").mkdir()
    (workspace / "package.json").write_text("{}")

    async def successful_executor(run_id, engine, mode, request, emit):
        assert run_id
        assert (engine, mode, request) == ("codex", "build", "Add a dashboard card")
        emit("assistant", "I changed the dashboard.")
        return 0, ""

    monkeypatch.setattr(builder, "workspace_changes", lambda _root=None: ["M frontend/a.tsx"])
    manager = builder.BuilderManager(
        executor=successful_executor,
        workspace=workspace,
        engine_probe=lambda: [
            {
                "id": "codex",
                "label": "Codex",
                "installed": True,
                "version": "codex-cli test",
                "command": "/opt/bin/codex",
                "build_is_sandboxed": True,
                "guidance": "",
            }
        ],
    )

    created = await manager.start("codex", "build", "Add a dashboard card")
    await manager.wait(created["id"])
    full = manager.get(created["id"])
    incremental = manager.get(created["id"], after=1)

    assert full["status"] == "succeeded"
    assert full["workspace_changes"] == ["M frontend/a.tsx"]
    assert any(e["text"] == "I changed the dashboard." for e in full["events"])
    assert all(e["seq"] > 1 for e in incremental["events"])


@pytest.mark.asyncio
async def test_manager_refuses_a_second_active_run(tmp_path):
    from backend.services import builder

    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "package.json").write_text("{}")
    release = asyncio.Event()

    async def slow_executor(_run_id, _engine, _mode, _request, _emit):
        await release.wait()
        return 0, ""

    manager = builder.BuilderManager(
        executor=slow_executor,
        workspace=tmp_path,
        engine_probe=lambda: [{"id": "codex", "installed": True, "command": "codex"}],
    )
    first = await manager.start("codex", "plan", "Review the architecture")

    with pytest.raises(builder.ActiveRunError, match=first["id"]):
        await manager.start("codex", "plan", "Plan another change")

    release.set()
    await manager.wait(first["id"])


@pytest.mark.asyncio
async def test_manager_surfaces_nonzero_exit_as_failed(tmp_path):
    from backend.services import builder

    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "package.json").write_text("{}")

    async def failed_executor(_run_id, _engine, _mode, _request, emit):
        emit("assistant", "Starting")
        return 2, "Login required"

    manager = builder.BuilderManager(
        executor=failed_executor,
        workspace=tmp_path,
        engine_probe=lambda: [{"id": "claude", "installed": True, "command": "claude"}],
    )
    created = await manager.start("claude", "plan", "Plan an import flow")
    await manager.wait(created["id"])
    run = manager.get(created["id"])

    assert run["status"] == "failed"
    assert run["error"] == "Login required"
    assert run["events"][-1]["kind"] == "error"


@pytest.mark.asyncio
async def test_manager_cancel_marks_run_and_cancels_executor(tmp_path):
    from backend.services import builder

    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "package.json").write_text("{}")
    started = asyncio.Event()

    async def never_finishes(_run_id, _engine, _mode, _request, _emit):
        started.set()
        await asyncio.Event().wait()
        return 0, ""

    manager = builder.BuilderManager(
        executor=never_finishes,
        workspace=tmp_path,
        engine_probe=lambda: [{"id": "codex", "installed": True, "command": "codex"}],
    )
    created = await manager.start("codex", "build", "Change the theme")
    await started.wait()

    cancelled = await manager.cancel(created["id"])

    assert cancelled["status"] == "cancelled"
    assert cancelled["finished_at"]
    assert cancelled["events"][-1]["text"] == "Run stopped."


@pytest.mark.asyncio
async def test_manager_validates_prompt_engine_and_workspace(tmp_path):
    from backend.services import builder

    manager = builder.BuilderManager(workspace=tmp_path, engine_probe=lambda: [])

    with pytest.raises(builder.BuilderValidationError, match="source checkout"):
        await manager.start("codex", "plan", "Inspect this")

    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "package.json").write_text("{}")
    with pytest.raises(builder.BuilderValidationError, match="Describe the change"):
        await manager.start("codex", "plan", "   ")
    with pytest.raises(builder.EngineUnavailableError, match="not installed"):
        await manager.start("codex", "plan", "Inspect this")


def test_builder_api_maps_status_start_poll_stop_and_errors(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import builder as builder_api
    from backend.services import builder

    class FakeManager:
        def status(self):
            return {
                "workspace": "/repo",
                "workspace_name": "repo",
                "engines": [],
                "active_run_id": None,
            }

        async def start(self, engine, mode, prompt):
            if prompt == "busy":
                raise builder.ActiveRunError("run-active")
            return {
                "id": "run-1",
                "engine": engine,
                "mode": mode,
                "prompt": prompt,
                "status": "queued",
                "events": [],
            }

        def get(self, run_id, after=0):
            if run_id == "missing":
                raise builder.RunNotFoundError(run_id)
            return {"id": run_id, "status": "running", "events": [{"seq": after + 1}]}

        async def cancel(self, run_id):
            return {"id": run_id, "status": "cancelled", "events": []}

    monkeypatch.setattr(builder_api, "builder_manager", FakeManager())
    app = FastAPI()
    app.include_router(builder_api.router)
    client = TestClient(app)

    assert client.get("/api/builder/status").status_code == 200
    started = client.post(
        "/api/builder/runs",
        json={"engine": "codex", "mode": "plan", "prompt": "Plan a card"},
    )
    assert started.status_code == 200
    assert started.json()["id"] == "run-1"
    assert client.get("/api/builder/runs/run-1?after=4").json()["events"] == [{"seq": 5}]
    assert client.delete("/api/builder/runs/run-1").json()["status"] == "cancelled"
    assert client.get("/api/builder/runs/missing").status_code == 404
    assert (
        client.post(
            "/api/builder/runs",
            json={"engine": "codex", "mode": "plan", "prompt": "busy"},
        ).status_code
        == 409
    )


# ── a public demo never starts a coding agent ───────────────────────────────────


def _demo_settings(monkeypatch):
    from backend.services import builder

    monkeypatch.setattr(
        builder, "get_settings", lambda: SimpleNamespace(desktop_mode=False, demo_mode=True)
    )
    return builder


@pytest.mark.asyncio
async def test_a_demo_manager_refuses_to_start_a_run_even_when_called_directly(
    tmp_path, monkeypatch
):
    """The route lockdown is one layer; this is the one that holds if a caller skips it."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "package.json").write_text("{}")
    builder = _demo_settings(monkeypatch)
    executed: list[str] = []

    async def executor(*_args):
        executed.append("ran")
        return 0, ""

    manager = builder.BuilderManager(
        executor=executor,
        workspace=tmp_path,
        engine_probe=lambda: [{"id": "codex", "installed": True, "command": "codex"}],
    )
    with pytest.raises(builder.EngineUnavailableError, match="public demo"):
        await manager.start("codex", "build", "Delete everything")
    assert executed == []
    assert manager.status()["active_run_id"] is None


@pytest.mark.asyncio
async def test_a_demo_never_spawns_the_engine_subprocess(monkeypatch):
    builder = _demo_settings(monkeypatch)
    spawned: list[tuple] = []

    async def no_spawn(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("a coding engine was spawned on a public demo")

    monkeypatch.setattr(builder.asyncio, "create_subprocess_exec", no_spawn)
    manager = builder.BuilderManager()
    with pytest.raises(builder.EngineUnavailableError, match="public demo"):
        await manager._execute_subprocess("run-1", "claude", "build", "x", lambda *_a: None)
    assert spawned == []


def test_a_demo_reports_no_engine_as_usable_without_probing_the_host(monkeypatch):
    builder = _demo_settings(monkeypatch)
    probed: list[str] = []
    monkeypatch.setattr(builder.shutil, "which", lambda name: probed.append(name) or "/bin/x")
    rows = builder.engine_statuses()
    assert probed == []
    assert not any(row["installed"] for row in rows)
    assert all("public demo" in row["guidance"] for row in rows)
    assert "public demo" in builder.BuilderManager().status()["error"]


def test_off_a_demo_the_builder_is_not_refused(monkeypatch):
    from backend.services import builder

    monkeypatch.setattr(
        builder, "get_settings", lambda: SimpleNamespace(desktop_mode=False, demo_mode=False)
    )
    builder._require_external_execution()
