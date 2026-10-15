"""An MCP server's URL is a credential (Zapier-style servers carry the token in the path), and an
error message from the transport reaches a model twice: fenced into the tool result, and stored
in ``connectors.state.last_error`` for ``get_connector_status``. Neither may quote the URL."""

from __future__ import annotations

import io
import urllib.error

import pytest

from backend.mcp.client import McpCallError, McpClientConnection
from backend.models.mcp_client import McpServerConfig

TOKEN = "Zk9aN3pTQ1JvM2xYd0Y5dTZ1Rm5hS2ZP"  # gitleaks:allow (fake token fixture)
URL = f"https://actions.example.com/api/mcp/s/{TOKEN}/mcp?api_key=QUERYSECRET"


def _config() -> McpServerConfig:
    return McpServerConfig(id="zap", name="Zap", transport="http", url=URL, enabled=True)


def _initialize_with(monkeypatch, error: Exception) -> McpCallError:
    def boom(request, timeout=None):
        raise error

    monkeypatch.setattr("backend.mcp.client._urlopen", boom)
    with pytest.raises(McpCallError) as caught:
        McpClientConnection(_config()).initialize()
    return caught.value


def _assert_no_credential(exc: McpCallError) -> None:
    for text in (str(exc), exc.safe_summary):
        assert TOKEN not in text
        assert "QUERYSECRET" not in text


def test_an_unreachable_server_is_named_by_host_only(monkeypatch):
    exc = _initialize_with(monkeypatch, OSError(f"connection refused by {URL}"))

    _assert_no_credential(exc)
    assert "actions.example.com" in str(exc)


def test_an_http_error_body_that_echoes_the_url_is_masked(monkeypatch):
    body = f'{{"error": "no such hook {URL}", "api_key": "BODYSECRET"}}'.encode()
    error = urllib.error.HTTPError(URL, 502, "Bad Gateway", {}, io.BytesIO(body))

    exc = _initialize_with(monkeypatch, error)

    _assert_no_credential(exc)
    assert "BODYSECRET" not in str(exc)
    assert "HTTP 502" in str(exc)


def test_an_http_error_body_is_kept_short(monkeypatch):
    error = urllib.error.HTTPError(URL, 500, "Boom", {}, io.BytesIO(b"x" * 5000))

    exc = _initialize_with(monkeypatch, error)

    assert len(str(exc)) < 400


def test_a_server_that_cannot_be_started_names_its_program_only(monkeypatch):
    from backend.mcp.stdio_client import StdioMcpClientConnection

    config = McpServerConfig(
        id="pg",
        name="Postgres",
        transport="stdio",
        command="/opt/tools/mcp-server",
        args=["postgresql://user:hunter2@db.example.com/prod"],
        enabled=True,
    )

    def refuse(*args, **kwargs):
        raise FileNotFoundError("[Errno 2] No such file or directory: '/opt/tools/mcp-server'")

    monkeypatch.setattr("backend.mcp.stdio_client.subprocess.Popen", refuse)
    with pytest.raises(McpCallError) as caught:
        with StdioMcpClientConnection(config) as connection:
            connection.initialize()

    assert "hunter2" not in str(caught.value)
    assert "mcp-server" in str(caught.value)
