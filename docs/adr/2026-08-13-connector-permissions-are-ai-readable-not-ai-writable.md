---
title: The connector surface is AI-READABLE, not AI-writable — no tool sets a permission, no tool adds a server
status: Accepted
date: 2026-08-13
deciders: repo owner (standing rules) + implementing Claude agent (escalated, not self-authorized)
---

# The connector surface is AI-READABLE, not AI-writable

## Context

The Connectors redesign built a per-tool permission ladder (`connector_tool_permissions`, ADR
`2026-08-13-connector-tool-permissions-storage`) and an external-MCP server surface that Lucius
could not see at all. That violated the standing principle — *"what can be triggered by native
code must also be triggerable by Lucius"*, and *new capabilities ship AI-reachable from day one*.

Making the surface reachable raises the obvious follow-on question: if Lucius can READ a
permission, why not SET one? And if it can read the MCP server list, why not ADD a server? Both
have a superficially clean safety story — a permission write could be confirm-gated like any other
write, and a newly added server's tools all default to `ask`, so adding one appears to grant no
immediate capability.

Both stories are wrong, for different reasons. This ADR records why, so the question is answered
once rather than relitigated each time someone notices the asymmetry.

## Decision

**The reads ship. The writes do not — structurally, not as a backlog item.**

Shipped (`backend/ai/tools/connector_config.py`, both `READ_ONLY_TOOLS`):

- `get_connector_permissions(connector_id, group?, limit?, offset?)` — one connector's tools
  grouped read vs. write, each with its real level, plus the group aggregate DERIVED via
  `services.tool_permissions.derive_group_level` (never stored).
- `list_mcp_servers(enabled_only?, limit?, offset?)` — configured external MCP servers, projected
  from `McpClientStore.public()` and re-allowlisted on top of it.

Deliberately absent, and pinned absent by
`tests/test_connector_config_tools.py::test_no_tool_writes_a_permission_or_adds_a_server`:
`set_tool_permission`, `set_group_permission`, `set_connector_permissions`,
`clear_connector_permissions`, `add_mcp_server`, `create_mcp_server`, `update_mcp_server`,
`probe_mcp_server`. A companion test asserts neither read tool advertises a `level`, `permission`,
`token`, `url` or `command` argument — a write smuggled in as a parameter would pass a name-based
sweep while doing the same damage.

### Why no permission write

A model that can raise its own tools to `allow` converts **one** approved confirm card into a
**permanent bypass of every future confirm card**. The ladder's entire value is that a human
decides, per tool, before the first call; a write tool would let the first call decide for all the
rest. The blast radius is the whole ladder, and the escalation is invisible — nothing after that
point would even raise a card to notice.

This codebase already establishes exactly this rule elsewhere: the sandbox's kill switches are
REST-only via `_AI_FORBIDDEN_UPDATE_KEYS`, and `chat_responder` is REST/user-settable only via
`_AI_FORBIDDEN_AGENT_KEYS` — *"so an agent can't grant itself responder status."* Permission writes
are the same shape of privilege and get the same treatment. Setting a level stays REST/UI only
(`api/connector_permissions.py`).

Reading is safe and genuinely useful, which is why the read half ships: Lucius can explain why a
call needed approval or was refused, and point the user at the exact control to change.

### Why no `add_mcp_server`

The "its tools all default to `ask`" argument is true (`DEFAULT_LEVEL == "ask"`, per-tool, no
exceptions) but is about the wrong object. The risk is not the tools the server advertises — it is
**the act of connecting**, which happens at probe time, before any tool is offered and before the
save a confirm card would gate:

1. **`transport="stdio"` is arbitrary local command execution on the ONW host.**
   `mcp/client.py::make_connection` branches "stdio → spawn" — the probe launches the configured
   command as a child of the backend process. A model-chosen `command`/`args` behind one confirm
   card showing a command string is a prompt-injection → RCE path. `models/mcp_client.py` already
   states the conclusion: *"Adding one is human-only (there is no Lucius tool that creates MCP
   servers)."*
2. **`transport="http"` is an unguarded SSRF primitive.** `mcp/client.py:209` calls
   `urllib.request.urlopen` directly; no file under `backend/mcp/` imports `core.net_guard` or
   `core.pinned_http`. A human-supplied URL makes that gap tolerable — a model-supplied one does
   not (cloud metadata, localhost, RFC1918), and the response is fed back to the model.
3. **It is not reachable from `ai/tools/` without breaking the seam.** `ai/` reaches outbound MCP
   only through `core.mcp_external`'s `ExternalDispatcher` protocol, which exposes exactly
   `schemas()` / `call()` / `apply_confirmed()` — no `probe`, no `create`. `ai/tools/external_mcp.py`
   follows that seam rather than importing `backend.mcp`, deliberately.

It is also settled product direction, not a fresh judgment call: RFC
`2026-08-10-connectors-architecture.md` §B4 — *"Lucius can guide, enable, and operate a connector
once it exists — setup itself stays human-only, everywhere"* — with `search_institutions` named as
the model of the boundary (fully callable, read-only, hands the linking back to the human).

## Consequences

- Lucius can explain and diagnose the permission ladder; it can never widen it. The only way a
  level changes is a human in Connectors.
- Lucius can enable, operate and report on connectors that already exist (`set_connector_enabled`,
  the per-provider status/sync tools) — the reach the principle actually asks for.
- Adding an MCP server stays a UI flow. If it is ever revisited, two prerequisites land first, both
  in the MCP lane's files, and it ships `http`-only: wire `assert_public_url`/`pinned_urlopen` into
  `backend/mcp/client.py`, and add a `probe` entry to the `ExternalDispatcher` protocol so `ai/`
  reaches it through the seam instead of importing `backend.mcp`.
- The absent-tool test is the guard. Reintroducing any of the eight names fails loudly rather than
  silently widening the surface.
