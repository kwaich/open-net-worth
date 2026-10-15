"""Desktop never starts detached local code processes before OS containment exists."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.config import Settings
from backend.mcp import stdio_client
from backend.mcp.client import McpCallError
from backend.models.mcp_client import McpServerConfig
from backend.sandbox import runner
from backend.services import builder


def never_spawn(*args, **kwargs):
    pytest.fail("Desktop capability gate attempted to launch a local process")


def test_desktop_mode_resolves_once_from_launch_environment(monkeypatch):
    monkeypatch.setenv("ONW_DESKTOP_MODE", "1")
    assert Settings().desktop_mode is True
    monkeypatch.delenv("ONW_DESKTOP_MODE")
    assert Settings().desktop_mode is False


@pytest.mark.parametrize("kind", ["source", "bundle"])
def test_desktop_sandbox_refuses_before_filesystem_or_spawn(monkeypatch, kind):
    monkeypatch.setattr(runner._paths, "DESKTOP_MODE", True)
    monkeypatch.setattr(runner.subprocess, "Popen", never_spawn)
    monkeypatch.setattr(runner.tempfile, "mkdtemp", never_spawn)
    with pytest.raises(runner.SandboxError, match="desktop app.*child-process containment"):
        if kind == "source":
            runner.run_source("print('synthetic')", {})
        else:
            runner.run_sandboxed("/missing-bundle", "v1", "run.py", {})


def test_desktop_stdio_mcp_refuses_before_spawn(monkeypatch):
    monkeypatch.setattr(stdio_client, "get_settings", lambda: SimpleNamespace(desktop_mode=True))
    monkeypatch.setattr(stdio_client.subprocess, "Popen", never_spawn)
    config = McpServerConfig(
        id="synthetic-local", name="Synthetic", transport="stdio", command="no"
    )
    with pytest.raises(McpCallError, match="desktop app.*Use an HTTP MCP server"):
        with stdio_client._StdioSession(config):
            pytest.fail("Local MCP session unexpectedly started")


@pytest.mark.asyncio
async def test_desktop_builder_refuses_start_and_subprocess_before_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(builder, "get_settings", lambda: SimpleNamespace(desktop_mode=True))
    monkeypatch.setattr(builder.asyncio, "create_subprocess_exec", never_spawn)
    monkeypatch.setattr(builder.subprocess, "run", never_spawn)
    manager = builder.BuilderManager(workspace=tmp_path)
    status = manager.status()
    assert status["workspace_valid"] is False
    assert "child-process containment" in status["error"]
    assert all(not engine["installed"] for engine in status["engines"])
    with pytest.raises(builder.EngineUnavailableError, match="desktop app.*containment"):
        await manager.start("codex", "build", "Synthetic request")
    with pytest.raises(builder.EngineUnavailableError, match="desktop app.*containment"):
        await manager._execute_subprocess("run-test", "claude", "plan", "Synthetic", never_spawn)
    assert manager._processes == {}
    assert manager._tasks == {}


def test_desktop_builder_api_surfaces_capability_error(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import builder as builder_api

    monkeypatch.setattr(builder, "get_settings", lambda: SimpleNamespace(desktop_mode=True))
    monkeypatch.setattr(builder.asyncio, "create_subprocess_exec", never_spawn)
    monkeypatch.setattr(builder.subprocess, "run", never_spawn)
    monkeypatch.setattr(builder_api, "builder_manager", builder.BuilderManager(workspace=tmp_path))
    app = FastAPI()
    app.include_router(builder_api.router)
    client = TestClient(app)
    assert "unavailable in the desktop app" in client.get("/api/builder/status").json()["error"]
    response = client.post(
        "/api/builder/runs", json={"engine": "codex", "mode": "plan", "prompt": "Synthetic"}
    )
    assert response.status_code == 409
    assert "child-process containment" in response.json()["detail"]
