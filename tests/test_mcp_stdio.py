"""P3-MCP (S4) — stdio<->HTTP bridge (RFC §S4).

Hermetic: NO real socket / server boot. The HTTP layer (``urllib.request.urlopen``)
is stubbed so every test exercises framing + error handling in-process. We feed a
newline-delimited JSON request on a fake binary stdin and assert the bridge writes
a correctly-framed JSON-RPC response to a fake binary stdout. (MCP stdio framing is
newline-delimited JSON — one message per line — NOT LSP Content-Length headers.)
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from backend.mcp import stdio_bridge


def _frame(payload: dict) -> bytes:
    """Encode a JSON-RPC message as one newline-delimited JSON line (MCP stdio)."""
    return json.dumps(payload).encode("utf-8") + b"\n"


def _parse_frames(raw: bytes) -> list[dict]:
    """Decode one-or-more newline-delimited JSON messages from a byte stream."""
    messages: list[dict] = []
    stream = io.BytesIO(raw)
    while True:
        body = stdio_bridge._read_message(stream)
        if body is None:
            break
        messages.append(json.loads(body))
    return messages


class _FakeResponse:
    """Minimal context-manager mimic of urlopen's return value."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def test_framing_round_trip(monkeypatch):
    """A framed request is forwarded; the canned HTTP response is framed back out."""
    canned = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "open-net-worth"}},
    }
    captured = {}

    def fake_urlopen(request, timeout=None, context=None):
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = request.data
        return _FakeResponse(json.dumps(canned).encode("utf-8"))

    monkeypatch.setattr(stdio_bridge.urllib.request, "urlopen", fake_urlopen)

    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    stdin = io.BytesIO(_frame(req))
    stdout = io.BytesIO()

    rc = stdio_bridge.run(stdin, stdout, "http://127.0.0.1:8080", "tok-abc")

    assert rc == 0
    # Forwarded to the right URL with the Bearer header + the verbatim body.
    assert captured["url"] == "http://127.0.0.1:8080/mcp"
    assert captured["auth"] == "Bearer tok-abc"
    assert json.loads(captured["body"])["method"] == "initialize"
    # Response framed back out, intact.
    frames = _parse_frames(stdout.getvalue())
    assert frames == [canned]


def test_multiple_messages_then_eof(monkeypatch):
    """Two framed requests are each forwarded + answered; clean EOF exits 0."""
    monkeypatch.setattr(
        stdio_bridge.urllib.request,
        "urlopen",
        lambda request, timeout=None, context=None: _FakeResponse(
            json.dumps(
                {"jsonrpc": "2.0", "id": json.loads(request.data)["id"], "result": {}}
            ).encode()
        ),
    )
    stdin = io.BytesIO(
        _frame({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        + _frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    )
    stdout = io.BytesIO()

    rc = stdio_bridge.run(stdin, stdout, "http://127.0.0.1:8080", "tok-abc")

    assert rc == 0
    frames = _parse_frames(stdout.getvalue())
    assert [f["id"] for f in frames] == [1, 2]


def test_malformed_json_emits_nothing(monkeypatch):
    """A framed body that isn't valid JSON -> NO output (and no HTTP call).

    An unparseable line has no recoverable JSON-RPC id; an ``{id:null, error}``
    frame is itself a message the client's schema rejects (the ZodError class), so
    the bridge stays silent on stdout (logged to stderr) instead of corrupting the
    stream. Regression for the Claude Desktop ``invalid_union`` connection error.
    """

    def fail_urlopen(request, timeout=None, context=None):  # pragma: no cover - must not be called
        raise AssertionError("urlopen must not be called for malformed input")

    monkeypatch.setattr(stdio_bridge.urllib.request, "urlopen", fail_urlopen)

    stdin = io.BytesIO(b"{ this is not json\n")
    stdout = io.BytesIO()

    rc = stdio_bridge.run(stdin, stdout, "http://127.0.0.1:8080", "tok-abc")

    assert rc == 0
    assert stdout.getvalue() == b""  # nothing written — never an id:null frame


def test_transport_failure_is_internal_error(monkeypatch):
    """urlopen raising (server down) -> a JSON-RPC internal-error frame with a useful message."""

    def boom(request, timeout=None, context=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(stdio_bridge.urllib.request, "urlopen", boom)

    req = {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}}
    stdin = io.BytesIO(_frame(req))
    stdout = io.BytesIO()

    rc = stdio_bridge.run(stdin, stdout, "http://127.0.0.1:8080", "tok-abc")

    assert rc == 0
    frames = _parse_frames(stdout.getvalue())
    assert len(frames) == 1
    assert frames[0]["id"] == 7  # error attributed to the originating request
    assert frames[0]["error"]["code"] == stdio_bridge.INTERNAL_ERROR
    msg = frames[0]["error"]["message"]
    assert "cannot reach" in msg and "Connection refused" in msg


def test_http_error_body_passed_through(monkeypatch):
    """A 401 from the server (its JSON body) is forwarded verbatim, not re-fabricated."""
    detail = {"detail": "authentication required"}

    def unauthorized(request, timeout=None, context=None):
        raise urllib.error.HTTPError(
            url=request.full_url,
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(json.dumps(detail).encode("utf-8")),
        )

    monkeypatch.setattr(stdio_bridge.urllib.request, "urlopen", unauthorized)

    req = {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
    stdin = io.BytesIO(_frame(req))
    stdout = io.BytesIO()

    rc = stdio_bridge.run(stdin, stdout, "http://127.0.0.1:8080", None)

    assert rc == 0
    frames = _parse_frames(stdout.getvalue())
    assert frames == [detail]


def test_eof_on_empty_stdin_exits_clean(monkeypatch):
    """No input at all (immediate EOF) -> clean exit 0, no output."""
    monkeypatch.setattr(
        stdio_bridge.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP on empty stdin")),
    )
    stdin = io.BytesIO(b"")
    stdout = io.BytesIO()

    rc = stdio_bridge.run(stdin, stdout, "http://127.0.0.1:8080", "tok-abc")

    assert rc == 0
    assert stdout.getvalue() == b""


def test_malformed_line_recovers_then_processes_next(monkeypatch):
    """A bad line -> NO output, but the bridge KEEPS reading and answers the next.

    A garbled message must not wedge the stream nor emit an unparseable frame: the
    bad line is silently skipped (logged to stderr) and the following valid request
    is answered normally — exactly one frame total, the good one.
    """
    monkeypatch.setattr(
        stdio_bridge.urllib.request,
        "urlopen",
        lambda request, timeout=None, context=None: _FakeResponse(
            json.dumps(
                {"jsonrpc": "2.0", "id": json.loads(request.data)["id"], "result": {}}
            ).encode()
        ),
    )
    stdin = io.BytesIO(
        b"{ not json\n" + _frame({"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {}})
    )
    stdout = io.BytesIO()

    rc = stdio_bridge.run(stdin, stdout, "http://127.0.0.1:8080", "tok-abc")

    assert rc == 0
    frames = _parse_frames(stdout.getvalue())
    assert len(frames) == 1  # the bad line emitted nothing
    assert frames[0]["id"] == 9  # the good line was still answered


def test_notification_emits_no_output(monkeypatch):
    """A JSON-RPC notification (no ``id``) -> the bridge writes NOTHING to stdout.

    THE bug regression: Claude Desktop sends ``notifications/initialized`` after
    ``initialize``. The bridge used to forward it, get the server's empty 202 body,
    and wrap that into ``{jsonrpc, id:null, error:...}`` — which the MCP message
    schema rejects (``invalid_union`` ZodError, ``context: "connection"``). The
    server returns no body for a notification; the bridge must emit no frame.
    """
    captured = {}

    def fake_urlopen(request, timeout=None, context=None):
        captured["forwarded"] = json.loads(request.data)
        return _FakeResponse(b"")  # the server's empty 202-style notification reply

    monkeypatch.setattr(stdio_bridge.urllib.request, "urlopen", fake_urlopen)

    stdin = io.BytesIO(_frame({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    stdout = io.BytesIO()

    rc = stdio_bridge.run(stdin, stdout, "http://127.0.0.1:8080", "tok-abc")

    assert rc == 0
    assert stdout.getvalue() == b""  # NO output for a notification
    # It was still forwarded best-effort (the server may act on it).
    assert captured["forwarded"]["method"] == "notifications/initialized"


def test_notification_forward_failure_still_no_output(monkeypatch):
    """Even if forwarding a notification fails (server down), the bridge emits nothing."""

    def boom(request, timeout=None, context=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(stdio_bridge.urllib.request, "urlopen", boom)

    stdin = io.BytesIO(_frame({"jsonrpc": "2.0", "method": "notifications/cancelled"}))
    stdout = io.BytesIO()

    rc = stdio_bridge.run(stdin, stdout, "http://127.0.0.1:8080", "tok-abc")

    assert rc == 0
    assert stdout.getvalue() == b""  # no id:null error frame for a notification


def test_ping_round_trips_empty_result(monkeypatch):
    """A ``ping`` request (has an id) -> forwarded; the server's ``{}`` result is framed back.

    The bridge reuses the HTTP endpoint's ``_handle_method``, which answers ping
    with an empty result (here stubbed). The bridge frames whatever the server
    returns for a real request, so ping keeps its id and result.
    """
    monkeypatch.setattr(
        stdio_bridge.urllib.request,
        "urlopen",
        lambda request, timeout=None, context=None: _FakeResponse(
            json.dumps(
                {"jsonrpc": "2.0", "id": json.loads(request.data)["id"], "result": {}}
            ).encode()
        ),
    )
    stdin = io.BytesIO(_frame({"jsonrpc": "2.0", "id": 5, "method": "ping", "params": {}}))
    stdout = io.BytesIO()

    rc = stdio_bridge.run(stdin, stdout, "http://127.0.0.1:8080", "tok-abc")

    assert rc == 0
    frames = _parse_frames(stdout.getvalue())
    assert frames == [{"jsonrpc": "2.0", "id": 5, "result": {}}]


def test_empty_body_for_real_request_emits_nothing(monkeypatch):
    """An empty/blank server body for a request with an id -> NO output (never id:null)."""
    monkeypatch.setattr(
        stdio_bridge.urllib.request,
        "urlopen",
        lambda request, timeout=None, context=None: _FakeResponse(b"  \n"),  # blank/whitespace body
    )
    stdin = io.BytesIO(_frame({"jsonrpc": "2.0", "id": 11, "method": "tools/list", "params": {}}))
    stdout = io.BytesIO()

    rc = stdio_bridge.run(stdin, stdout, "http://127.0.0.1:8080", "tok-abc")

    assert rc == 0
    assert stdout.getvalue() == b""  # no fabricated id:null frame


def test_full_handshake_sequence_no_id_null_anywhere(monkeypatch):
    """The realistic Claude Desktop opening sequence: initialize, notifications/initialized,
    tools/list, resources/list -> EXACTLY 3 framed replies (ids 0,1,2), none with id:null,
    none an error for the notification."""

    def fake_urlopen(request, timeout=None, context=None):
        msg = json.loads(request.data)
        if "id" not in msg:  # the server returns an empty body for a notification
            return _FakeResponse(b"")
        method = msg["method"]
        result = {"protocolVersion": "2025-06-18"} if method == "initialize" else {method: []}
        return _FakeResponse(
            json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}).encode()
        )

    monkeypatch.setattr(stdio_bridge.urllib.request, "urlopen", fake_urlopen)

    stdin = io.BytesIO(
        _frame(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
            }
        )
        + _frame({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + _frame({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        + _frame({"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}})
    )
    stdout = io.BytesIO()

    rc = stdio_bridge.run(stdin, stdout, "http://127.0.0.1:8080", "tok-abc")

    assert rc == 0
    frames = _parse_frames(stdout.getvalue())
    assert [f["id"] for f in frames] == [0, 1, 2]  # the notification produced no frame
    assert all(f.get("id") is not None for f in frames)  # never id:null
    assert all("error" not in f for f in frames)  # no error for the notification


def test_handle_line_pure(monkeypatch):
    """The extracted pure helper: notification -> None, real request -> a JSON string."""

    def fake_urlopen(request, timeout=None, context=None):
        msg = json.loads(request.data)
        if "id" not in msg:  # the server returns an empty body for a notification
            return _FakeResponse(b"")
        return _FakeResponse(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}).encode())

    monkeypatch.setattr(stdio_bridge.urllib.request, "urlopen", fake_urlopen)
    notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
    assert stdio_bridge.handle_line(notif, "http://h", "t") is None

    req = json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping", "params": {}}).encode()
    out = stdio_bridge.handle_line(req, "http://h", "t")
    assert isinstance(out, str)
    assert json.loads(out) == {"jsonrpc": "2.0", "id": 4, "result": {}}


@pytest.mark.parametrize(
    "argv,env,expected",
    [
        ([], {}, ("http://127.0.0.1:8080", None)),
        (
            [],
            {"ONW_BASE_URL": "http://host:9000", "ONW_API_TOKEN": "t1"},
            ("http://host:9000", "t1"),
        ),
        (
            ["--base-url", "http://cli:1", "--token", "ct"],
            {"ONW_BASE_URL": "http://env:2"},
            ("http://cli:1", "ct"),
        ),
        (["--base-url=http://eq:3", "--token=eqtok"], {}, ("http://eq:3", "eqtok")),
    ],
)
def test_config_precedence(monkeypatch, argv, env, expected):
    """argv overrides env overrides defaults for base_url + token."""
    for key in ("ONW_BASE_URL", "ONW_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert stdio_bridge._parse_config(argv) == expected
