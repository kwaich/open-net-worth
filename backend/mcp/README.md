# MCP client setup

Open Net Worth exposes a Bearer-gated, **read-only** MCP server at `POST /mcp`
(see `server.py` / `adapter.py`). There are two ways to connect a client.

## 1. HTTP clients (remote-capable: IDEs, phone, Claude Desktop's remote MCP)

Point the client straight at the running instance's `/mcp` endpoint and send the
API token as a Bearer header:

```json
{
  "url": "https://<your-host>/mcp",
  "headers": { "Authorization": "Bearer <API_TOKEN>" }
}
```

The API token is the one in `auth.json` (`api_token`) — set it via
`AUTH_API_TOKEN` or copy the value the app logs once on first boot. Never put
the token in the URL (logs/proxies leak query strings).

## 2. stdio-only clients (Claude Desktop, some IDEs/CLIs) — the stdio bridge

Clients that only speak **stdio** spawn a child process and exchange JSON-RPC
over its stdin/stdout. `backend/mcp/stdio_bridge.py` is a tiny stdlib-only
process that bridges that stdio transport to the HTTP `/mcp` endpoint: it reads
newline-delimited JSON-RPC requests from stdin (the MCP stdio framing — one JSON
message per line), forwards each as `POST {base_url}/mcp` with
`Authorization: Bearer <token>`, and writes the HTTP response back to stdout as
one newline-terminated line.

The bridge **adds no surface and no dependency** — it can only forward to `/mcp`,
which is already read-only and Bearer-gated. The Open Net Worth server must be
running (`uvicorn backend.main:app --port 8080`) for the bridge to reach it.

### Claude Desktop config

Add this to `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "open-net-worth": {
      "command": "/absolute/path/to/my-net-worth/.venv/bin/python3",
      "args": ["-m", "backend.mcp.stdio_bridge"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/my-net-worth",
        "ONW_BASE_URL": "http://127.0.0.1:8080",
        "ONW_API_TOKEN": "<API_TOKEN>"
      }
    }
  }
}
```

Notes:
- Use **`PYTHONPATH`** (the repo root), not `cwd`, to make `backend` importable —
  Claude Desktop does **not** reliably honor the `cwd` field, so relying on it
  fails with `ModuleNotFoundError: No module named 'backend'`. `PYTHONPATH` works
  regardless of where the client spawns the process.
- Point `command` at the project venv's interpreter (absolute path) so the client
  can't pick up an unrelated `python3` from `PATH` (e.g. Xcode's).
- `ONW_BASE_URL` defaults to `http://127.0.0.1:8080` (the dev default) and can be
  omitted if your instance listens there.
- `ONW_API_TOKEN` is required unless the target runs with `AUTH_DISABLED=true`.
- Both settings also accept argv overrides: `--base-url <url>` / `--token <tok>`
  (argv > env > default).

### Error behavior

- Malformed input → a JSON-RPC parse-error frame (the bridge never crashes).
- The server being down / unreachable → a JSON-RPC internal-error frame with a
  clear "cannot reach … /mcp" message.
- A non-2xx HTTP status (e.g. 401 on a bad/missing token) → the server's own
  JSON error body is passed through verbatim.
- Clean EOF on stdin (client closed the pipe) → exit 0.
