"""stdio <-> HTTP bridge for the Open Net Worth MCP server (RFC P3-MCP §S4).

Many local MCP clients (Claude Desktop, some IDEs/CLIs) only speak **stdio** —
they spawn a child process and exchange JSON-RPC messages over its stdin/stdout.
Our MCP server is an HTTP endpoint (``POST /mcp``, Bearer-gated). This module is
the tiny glue that lets a stdio-only client reach it: it reads framed JSON-RPC
requests from stdin, forwards each verbatim to ``{base_url}/mcp`` with the API
token as a Bearer header, and writes the HTTP response back to stdout with the
same framing.

It is **pure I/O glue** — zero business logic, no money math, and no new tool
surface. It can only forward to ``/mcp``, which is already read-only (S1/S3) and
Bearer-gated; the bridge adds no local auth bypass and widens nothing. Stdlib
only (``sys``/``json``/``os``/``urllib``) — NO ``mcp`` SDK, no new dependency.

Installed copies run it as ``onw mcp`` (``backend/cli/mcp.py``), which also finds a desktop
app's per-launch port. From a source checkout it still runs as a module::

    ONW_BASE_URL=http://127.0.0.1:8080 ONW_API_TOKEN=<token> \
        python3 -m backend.mcp.stdio_bridge

Config precedence: argv (``--base-url`` / ``--token``) overrides env
(``ONW_BASE_URL`` / ``ONW_API_TOKEN``) overrides the default base URL. The token
is required unless the target instance runs with ``AUTH_DISABLED=true``.

Framing: MCP stdio uses **newline-delimited JSON** — each JSON-RPC message is a
single line of UTF-8 JSON terminated by ``\n`` and MUST NOT contain an embedded
newline. We read one line per message off stdin and write **at most one** line of
reply to stdout (a notification or an empty server reply produces none); the JSON
payload of a real request is forwarded to the HTTP endpoint untouched.
(This is the MCP stdio transport — NOT the LSP ``Content-Length`` header framing,
which a previous version wrongly used and which made every client hang on
``initialize``.)

Robustness contract:
  * a JSON-RPC **notification** (a message with no ``id``) -> dispatched
    best-effort and answered with **NOTHING** on stdout. A notification MUST NOT
    produce a response; replying with an ``{id:null,error}`` frame is exactly the
    bug the HTTP endpoint fixed in ``cdbc279`` (the client's message schema has no
    ``error``-with-null-id member and rejects it with a Zod ``invalid_union``).
    The server already returns an empty 202-style body for a forwarded
    notification — the bridge MUST NOT turn that empty body into an error frame.
  * malformed bytes that don't parse as JSON, with **no recoverable id**, -> NO
    output (the client can't parse an ``id:null`` error either); logged to stderr.
  * the HTTP server being down / a transport error on a REAL request (one with an
    ``id``) -> a JSON-RPC internal-error frame with a clear message (never a
    stack-trace crash).
  * clean EOF on stdin (the client closed the pipe) -> exit 0.
A bridge that crashes mid-session would look like a dead MCP server to the
client, so every per-message failure with a valid request id is turned into a
well-formed error frame — but when there is no id to attribute it to, we emit
NOTHING rather than a malformed ``id:null`` line that corrupts the stream.

**stdio hygiene:** stdout is the EXCLUSIVE JSON-RPC channel. NOTHING else is ever
written to it — all diagnostics go to **stderr** (``_log``). A single stray
stdout line corrupts the stream and trips a client message-parse error, so even
the entrypoint configures ``logging`` to stderr and never ``print``s.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from typing import Any, BinaryIO, Callable, Optional, Union

from backend.core.pinned_http import https_context

DEFAULT_BASE_URL = "http://127.0.0.1:8080"

# JSON-RPC 2.0 standard error codes (mirrors backend/mcp/server.py).
PARSE_ERROR = -32700
INTERNAL_ERROR = -32603

# Bound the HTTP forward so a wedged server can't hang the bridge forever.
HTTP_TIMEOUT_SECONDS = 120

logger = logging.getLogger(__name__)


def _log(message: str) -> None:
    """Diagnostics go to STDERR only — stdout is the exclusive JSON-RPC channel."""
    logger.warning(message)


# --------------------------------------------------------------------------- #
# Framing (MCP stdio: newline-delimited JSON, one message per line)           #
# --------------------------------------------------------------------------- #
def _read_message(stream: BinaryIO) -> Optional[bytes]:
    """Read one newline-delimited JSON message from ``stream``.

    Returns the raw JSON line bytes (newline stripped), or ``None`` on clean EOF
    (the client closed the pipe). Blank lines are skipped — some clients emit
    them as keep-alives and they carry no message.
    """
    while True:
        line = stream.readline()
        if not line:
            return None  # clean EOF — the client closed stdin.
        stripped = line.strip()
        if not stripped:
            continue  # skip blank/keep-alive lines.
        return stripped


def _write_message(stream: BinaryIO, payload: str) -> None:
    """Write one newline-delimited JSON-RPC message (a JSON string) to ``stream`` and flush.

    The payload is a single-line JSON string (``handle_line`` already serialized
    it); a JSON-RPC message never contains an embedded newline, so the single
    trailing ``\n`` is an unambiguous message delimiter.
    """
    stream.write(payload.encode("utf-8") + b"\n")
    stream.flush()


# --------------------------------------------------------------------------- #
# Error envelopes                                                             #
# --------------------------------------------------------------------------- #
def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _request_id(body: bytes) -> Any:
    """Best-effort extract the JSON-RPC ``id`` from a request body for error replies."""
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, dict):
        return parsed.get("id")
    return None


def _is_notification(parsed: Any) -> bool:
    """A JSON-RPC notification is a request object with **no** ``id`` member.

    Per the spec a notification MUST NOT receive a response — including no error
    response. ``notifications/initialized`` (which Claude Desktop sends right
    after ``initialize``) is the common case. ``id: null`` is a present-but-null
    id (a malformed request), NOT a notification — so we key on key PRESENCE.
    """
    return isinstance(parsed, dict) and "id" not in parsed


# --------------------------------------------------------------------------- #
# HTTP forward                                                                #
# --------------------------------------------------------------------------- #
def _forward(body: bytes, base_url: str, token: Optional[str]) -> bytes:
    """POST the JSON-RPC request body to ``{base_url}/mcp``; return the response body bytes.

    Raises ``urllib.error.URLError``/``OSError`` on a transport failure; an HTTP
    error status (e.g. 401) still carries a JSON-RPC-shaped body from the server,
    which we read off the ``HTTPError`` and pass through unchanged.
    """
    url = base_url.rstrip("/") + "/mcp"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(
            request, timeout=HTTP_TIMEOUT_SECONDS, context=https_context()
        ) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        # The server replied with a non-2xx status (e.g. 401 unauthorized). Its
        # body is the JSON-RPC / detail envelope — forward it verbatim so the
        # client sees the real error instead of a bridge-fabricated one.
        return exc.read()


# --------------------------------------------------------------------------- #
# Per-message handling                                                        #
# --------------------------------------------------------------------------- #
def handle_line(line: bytes, base_url: str, token: Optional[str]) -> Optional[str]:
    """Handle ONE stdin line → the JSON string to write back, or ``None`` for no output.

    This is the pure, testable heart of the bridge (the loop is just framing I/O
    around it). The contract that fixes the Claude Desktop ZodError:

      * **Notification** (parsed dict with no ``id`` key, e.g.
        ``notifications/initialized``) → forward best-effort so the server can act
        on it, but return ``None`` — the bridge writes NOTHING. We never fabricate
        an ``{id:null,error}`` frame for a notification.
      * **Unparseable line** (not JSON) → ``None`` (logged to stderr). There is no
        recoverable id, and an ``id:null`` parse-error frame is itself a message
        the client can't parse, so emitting one would just trip another error.
      * **Real request** (has an ``id``) → forward and return the server's response
        body. A transport failure becomes an internal-error frame attributed to
        that id. An empty/blank server body (the server's 202-for-notification, or
        any no-content reply) → ``None`` (never wrapped into an ``id:null`` error).
    """
    try:
        parsed = json.loads(line)
    except (ValueError, TypeError):
        _log(f"skipping unparseable stdin line (no recoverable id): {line[:200]!r}")
        return None

    notification = _is_notification(parsed)
    try:
        response_body = _forward(line, base_url, token)
    except (urllib.error.URLError, OSError) as exc:
        if notification:
            # A notification gets no reply even if the forward failed — just log.
            _log(f"notification forward failed (no reply emitted): {exc}")
            return None
        reason = getattr(exc, "reason", exc)
        message = f"cannot reach Open Net Worth MCP server at {base_url}/mcp: {reason}"
        return json.dumps(_rpc_error(_request_id(line), INTERNAL_ERROR, message))

    if notification:
        # The server's notification reply is an empty 202 body — emit nothing.
        return None

    if not response_body or not response_body.strip():
        # An empty/blank body for a real request: no message to frame. Don't
        # fabricate an id:null error (the client can't parse it). Stay silent.
        _log(f"empty server body for request id {_request_id(line)!r}; no frame emitted")
        return None

    # Validate the server gave us JSON; if not, wrap it as an internal error keyed
    # to the real request id (which we have, since this is not a notification).
    try:
        json.loads(response_body)
    except (ValueError, TypeError):
        return json.dumps(
            _rpc_error(_request_id(line), INTERNAL_ERROR, "malformed response from MCP server")
        )
    return response_body.decode("utf-8")


# --------------------------------------------------------------------------- #
# Config + entrypoint                                                         #
# --------------------------------------------------------------------------- #
def _parse_config(argv: list[str]) -> tuple[str, Optional[str]]:
    """Resolve (base_url, token) from argv (highest) then env then defaults."""
    base_url = os.environ.get("ONW_BASE_URL", "").strip() or DEFAULT_BASE_URL
    token = os.environ.get("ONW_API_TOKEN", "").strip() or None

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--base-url" and i + 1 < len(argv):
            base_url = argv[i + 1].strip() or base_url
            i += 2
        elif arg.startswith("--base-url="):
            base_url = arg.split("=", 1)[1].strip() or base_url
            i += 1
        elif arg == "--token" and i + 1 < len(argv):
            token = argv[i + 1].strip() or token
            i += 2
        elif arg.startswith("--token="):
            token = arg.split("=", 1)[1].strip() or token
            i += 1
        else:
            i += 1
    return base_url, token


def run(
    stdin: BinaryIO,
    stdout: BinaryIO,
    base_url: Union[str, Callable[[], str]],
    token: Optional[str],
) -> int:
    """The bridge loop: read framed requests from ``stdin``, forward, frame replies to ``stdout``.

    ``base_url`` may be a callable, resolved per message (``onw mcp`` follows a desktop app
    whose port changes on every launch).

    Returns a process exit code (0 on a clean stdin EOF). Each message is handled
    independently — a notification / bad frame / empty reply yields NO output, a
    real request yields exactly one framed reply, and nothing is ever a
    loop-breaking exception.
    """
    while True:
        body = _read_message(stdin)
        if body is None:
            return 0  # clean EOF — the client closed stdin.

        url = base_url() if callable(base_url) else base_url
        reply = handle_line(body, url, token)
        if reply is not None:
            _write_message(stdout, reply)


def main(argv: Optional[list[str]] = None) -> int:
    # stdout is the EXCLUSIVE JSON-RPC channel — pin ALL logging to stderr so a
    # stray log line can never corrupt the message stream (the corruption class
    # that produces exactly the client-side message-parse error this fixes).
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)
    base_url, token = _parse_config(argv if argv is not None else sys.argv[1:])
    # Use the raw binary buffers so framing byte counts are exact (no newline
    # translation / text-codec surprises across platforms).
    return run(sys.stdin.buffer, sys.stdout.buffer, base_url, token)


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
