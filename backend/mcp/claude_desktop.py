"""Parse a Claude-Desktop ``mcpServers`` config block into ``McpServerConfig`` records.

Claude Desktop (and several IDEs/CLIs) configure MCP servers with a JSON block like::

    {
      "mcpServers": {
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]},
        "github":     {"command": "uvx", "args": ["mcp-server-github"], "env": {"GITHUB_TOKEN": "ghp_…"}},
        "remote":     {"url": "https://mcp.example.com/mcp"}
      }
    }

This is the exact shape a self-hoster already has on hand, so accepting it verbatim is the
lowest-friction "bring your own local MCP server" path. This module is a PURE function —
dict → ``list[McpServerConfig]`` — with no I/O (the caller persists). Each entry maps:

  * a ``command`` entry  → a **stdio** server (``command``/``args``/``env`` carried verbatim);
  * a ``url`` entry      → an **http** server (the existing remote transport).

The map KEY becomes the server id (via the shared ``slug_server_id``/``unique_server_id``
helpers the add flow's own auto-slug uses, so both paths mint ids the same way). New servers
are created **disabled**, with every tool at the ``ask`` default — the user enables each and
grants what it needs in the UI, exactly like the manual add path. A malformed entry is
SKIPPED (reported), never fabricated.
"""

from __future__ import annotations

from typing import Iterable, Optional

from backend.models.mcp_client import McpServerConfig, slug_server_id, unique_server_id


def parse_claude_desktop_config(
    data: dict, *, existing_ids: Optional[Iterable[str]] = None
) -> tuple[list[McpServerConfig], list[str]]:
    """Map a Claude-Desktop config block → ``(configs, skipped_reasons)``.

    ``data`` is the parsed JSON (either the whole ``{"mcpServers": {...}}`` doc or the bare
    ``{name: entry}`` map). Returns the created configs (disabled, every tool at the ``ask``
    default) plus a list of human-readable reasons for any skipped entry. Never raises on a
    malformed entry — it is skipped and reported.
    """
    if not isinstance(data, dict):
        return [], ["config is not a JSON object"]
    servers = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else data
    if not isinstance(servers, dict) or not servers:
        return [], ["no 'mcpServers' entries found"]

    taken = {str(i) for i in (existing_ids or [])}
    configs: list[McpServerConfig] = []
    skipped: list[str] = []

    for name, entry in servers.items():
        if not isinstance(entry, dict):
            skipped.append(f"{name}: not an object")
            continue
        server_id = unique_server_id(slug_server_id(str(name)), taken)
        taken.add(server_id)
        command = str(entry.get("command") or "").strip()
        url = str(entry.get("url") or "").strip()
        try:
            if command:
                raw_args = entry.get("args")
                args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
                raw_env = entry.get("env")
                env = (
                    {str(k): str(v) for k, v in raw_env.items()}
                    if isinstance(raw_env, dict)
                    else {}
                )
                configs.append(
                    McpServerConfig(
                        id=server_id,
                        name=str(name),
                        transport="stdio",
                        command=command,
                        args=args,
                        env=env,
                        enabled=False,
                    )
                )
            elif url:
                configs.append(
                    McpServerConfig(
                        id=server_id,
                        name=str(name),
                        transport="http",
                        url=url,
                        enabled=False,
                    )
                )
            else:
                skipped.append(f"{name}: no 'command' or 'url'")
        except ValueError as exc:  # pydantic id/shape validation
            skipped.append(f"{name}: {exc}")

    return configs, skipped
