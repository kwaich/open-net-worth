---
title: External-MCP stdio connections are reused for the life of a turn, not respawned per call
status: Superseded
date: 2026-08-22
deciders: owner + implementing Claude agent
---

> Superseded by [2026-08-22-external-mcp-stdio-connections-persist-for-the-app-lifetime](2026-08-22-external-mcp-stdio-connections-persist-for-the-app-lifetime.md).
> Per-turn reuse fixed the within-one-message case (Playwright's navigate → screenshot) but
> did not survive BETWEEN chat messages — a real-world server (interactive-brokers-mcp)
> needs its child process, and the authenticated gateway session tied to it, to survive
> across a whole conversation, not just one turn. The superseding ADR keeps every mechanism
> below that is still correct (dead-connection detection + reconnect, the permission-gate
> independence argument, HTTP staying out of scope) and replaces only the pool's SCOPE.

# External-MCP stdio connections are reused for the life of a turn, not respawned per call

## Context

`McpClientManager._fire` — the real per-tool-call dispatch path for an external MCP
server — did this on every single call:

```python
conn = make_connection(server)
text = await asyncio.to_thread(conn.call_tool, tool, args or {})
```

No `with` block, no storage, no reuse. `make_connection`'s own docstring says to use it as
`with make_connection(cfg) as conn:` "whenever the caller makes more than one call: for
stdio that keeps ONE child process for the whole block" — `_fire` never did that, so every
stdio call spawned a brand-new child process (`StdioMcpClientConnection`'s stateless-per-
call fallback path), and every HTTP call built a brand-new `McpClientConnection`.

This breaks any server whose state lives on the client **session**, i.e. the connection's
lifetime — verified against a real, ordinary catalog entry: Playwright MCP
(`npx @playwright/mcp@latest`, `MCP_INSTALL_PRESETS['mcp-playwright']`). Playwright ties an
open browser page/context to the client session (confirmed mechanism:
`microsoft/playwright-mcp#1045`). Since this app respawned the child on every call,
`browser_navigate` then `browser_screenshot` as two Lucius tool calls handed the second
call a blank, memory-less browser with zero knowledge of the navigation that just
happened — 100% reproducible, not flaky.

Read against a real MCP client for comparison (`openai/codex`,
`codex-rs/codex-mcp/src/connection_manager.rs`): Codex holds one persistent connection per
configured server for the life of the session, with an explicit `reusable_client()` check
(matching config, not closed, credentials still valid) before ever creating a new one, torn
down only on `Drop`. Per-call respawn is a genuine outlier in this app, not a defensible
alternative design.

## Decision

### Scope: one connection per (turn, server), not per session, not per process

A chat/Automation **turn** (`backend/ai/turns.py` — `spawn()`/`run_inline()`, both funneling
through `_drive`) is the natural unit: a turn's own tool-dispatch loop already runs several
sequential tool calls in place — exactly where Playwright's navigate → screenshot sequence
happens — so per-turn persistence fixes the reported bug completely.

Deliberately NOT per-session-across-many-turns, and NOT for the life of the process: both
raise a genuinely separate, unscoped question ("how long should a connection survive idle
between UNRELATED turns, hours apart" — reconnect-on-stale semantics, idle-timeout tuning)
that this fix does not need to answer. If a turn ends and a new one starts, spawning a fresh
child is fine and expected — exactly the pre-fix behavior, just now bounded to "once per
turn" instead of "once per call".

### Mechanism: a ContextVar carries the turn id down to the transport factory

`backend/core/mcp_turn_context.py` (new leaf, mirrors `core.tool_use_context`) exposes
`set_turn_id`/`get_turn_id`. `ai/turns.py::_drive` sets it right after `set_cost_surface`
(the existing first-statement pattern that keeps the confirm gate from failing open) and
clears it in `finally`. A `ContextVar` — not a parameter threaded through
`registry.dispatch` → the `core.mcp_external` seam → the manager → the transport factory —
because `asyncio.to_thread` (which `_fire` already uses to run the blocking transport call)
copies the calling `contextvars.Context` into the worker thread, so the blocking code that
actually spawns/reuses the child can read it with zero signature changes anywhere in that
chain.

`McpClientManager` gained `self._turn_conns: dict[(turn_id, server_id), StdioMcpClientConnection]`
guarded by a `threading.Lock` (entries are created/evicted from worker threads). `_fire`'s
blocking half (`_call_tool_blocking`) now:

- for HTTP: unchanged — one connection per call (see "HTTP is out of scope" below);
- for stdio with no turn driving (`get_turn_id()` is `None` — a caller that predates
  `ai/turns.py`'s lifecycle): unchanged stateless-per-call fallback, because nothing would
  ever call `end_turn` for a connection opened outside a turn, and pooling one anyway would
  leak a child process for the life of the *process* instead of the life of a *turn*;
- for stdio inside a turn: look up `(turn_id, server_id)`. If cached and `is_alive()` (a
  cheap `proc.poll()`), reuse it. Otherwise open one, `__enter__()` it (spawns the child,
  same as an interactive `with make_connection(...)` block does today), and cache it before
  calling.

### Dead-connection recovery: detect and evict, never loop-retry

`is_alive()` (added to `StdioMcpClientConnection`/its `_StdioSession`) catches the common
case (child already exited — crashed, or killed by its own call's timeout watchdog) before
reuse is even attempted. A rarer case — a broken pipe the OS hasn't yet reaped into an exit
status, so `is_alive()` still says healthy — is caught the other way: any `McpCallError`
from `call_tool` evicts the cached entry regardless. Either way, the call that hit the dead
connection still fails and is fenced back to the model as usual (no silent same-call retry —
that would risk a tool firing twice against a server that might have actually processed the
first attempt); the **next** call to that server, later in the same turn, is guaranteed a
fresh, working connection instead of repeating the same failure for the rest of the turn.

### Teardown: a seam hook, not a direct `ai` → `mcp` import

`backend/mcp/` sits outside the `api → ai → services → db → core` layered contract (it is
its own `root_packages` entry in `pyproject.toml`'s import-linter config) and is reached by
`ai` **only** through the `core.mcp_external` dispatcher seam
(`register_external_dispatcher`/`get_external_dispatcher`) — never a direct import, so `ai`
stays decoupled from the concrete `backend.mcp` implementation. This fix's teardown hook
follows the same shape rather than adding a new edge:

- `core.mcp_external.ExternalDispatcher` gains an `end_turn(turn_id)` method (declared on
  the Protocol for documentation, but consumed defensively via
  `getattr(dispatcher, "end_turn", None)` — a dispatcher or test fake that predates this
  hook is not obligated to implement it);
- `core.mcp_external.end_turn_scope(turn_id)` is the module-level entry point `ai/turns.py`
  calls: no-op if no dispatcher is registered or the registered one has no `end_turn`, and
  **never raises** — a stuck child that resists teardown must not fail (or even delay
  reporting the outcome of) a turn that already finished its real work, mirroring
  `connectors.telegram.bot._stop_locked`'s "best-effort but never silently swallow a
  failure" discipline (the dispatcher's own `end_turn` is expected to log a per-connection
  failure itself; the seam wrapper is the last line of defense against a totally unexpected
  exception escaping it);
- `McpClientManager.end_turn(turn_id)` closes every connection cached under that turn id,
  best-effort per connection (one stuck child does not stop the others from being closed);
- `_drive`'s `finally` block calls `await end_turn_scope(handle.turn_id)` for **every**
  terminal path — normal completion, error, timeout, AND cancellation (a Stop must not leak
  a connection) — before clearing the turn-id ContextVar. Since `spawn()` and `run_inline()`
  both funnel through `_drive` (ADR
  `2026-08-06-one-turn-lifecycle-for-chat-and-automations`), a chat turn and an Automation
  run get identical cleanup with no separate wiring. Process-shutdown cleanup falls out of
  the SAME mechanism for free: `main.py`'s lifespan already calls `turns.shutdown()`, which
  cancels every registered turn and awaits it — driving each one's `_drive` `finally` (and
  therefore `end_turn_scope`) exactly as an ordinary cancellation would, with no new
  shutdown-specific code needed.

### HTTP is out of scope, on inspection rather than assumption

Investigated rather than assumed: `_call_tool_blocking` never called `initialize()` on the
HTTP transport before `_fire` existed, and still doesn't — only `tools/call` is sent, so no
`Mcp-Session-Id` is ever negotiated on this path regardless of connection lifetime. Pooling
the HTTP connection object would therefore change nothing observable today; there is no
process to reuse (the concrete cost this fix targets), and no established session state a
persistent connection would preserve. If a future HTTP server needs session affinity, that
is a separate, currently-nonexistent gap (this method would need to start calling
`initialize()` at all first), not something to paper over here.

## Security: the permission/confirm-gate model is unchanged

`call_external_tool` resolves `tool_permissions.get_tool_permission(...)` and only reaches
`_fire` (where connection reuse lives) on `always_allow`; `needs_approval` always parks a
proposal via `PendingPlanStore` first (`_pending.stage(...)`), and `apply_confirmed(nonce)`
re-resolves the level again before calling `_fire` a second time. **Connection reuse lives
entirely inside `_fire`'s blocking half — strictly downstream of every one of those
checks.** Nothing about caching a `StdioMcpClientConnection` object changes when or whether
that gate runs: it is consulted fresh on every `call_external_tool`/`apply_confirmed`
invocation, independent of whether the underlying child process is a new spawn or a reused
one. Regression-tested directly
(`tests/test_mcp_turn_connection_reuse.py::test_permission_gate_still_runs_independently_per_call_on_a_reused_connection`):
two calls to the same `needs_approval` tool in one turn both produce a fresh nonce and both
require their own `apply_confirmed`, even though both ultimately run on the same reused
child process.

The nonce itself is unaffected by any of this: `McpPendingCallStore.stage`/`pop` key on a
server-minted nonce string, never on a connection object or any per-connection identity —
there was nothing to move onto the new pooled connection and nothing new for a connection to
leak into.

**What DOES change, worth naming even though it is not a gate bypass**: a reused connection
means a human-approved call's underlying session state may have been shaped by EARLIER,
UNGATED (`always_allow`) calls in the same turn that the operator never saw a confirm card
for. Every tool's permission level still resolves independently and nothing skips a check —
but before this ADR, every call got a brand-new child with no prior state at all, so a
confirm card's displayed args were the WHOLE story of what was about to run. Now, within one
turn, an `always_allow` tool earlier in that turn can have already changed what the
connection's session looks like (e.g. Playwright's open page/context) by the time a
`needs_approval` call later in the same turn parks a card for it — the args on that card are
still exactly what will be sent, but the session they run against is no longer guaranteed
blank. This is a property of session reuse generally, not something this fix introduces
carelessly: it's the direct, necessary consequence of fixing the Playwright-style bug at
all, and is worth an operator's awareness rather than a silent assumption that every call
still starts from zero.

## Consequences

- Fixes the reported Playwright-MCP-style bug for the in-scope case (a turn's own sequential
  tool calls), but the benefit is scoped to `always_allow` tools within ONE turn, not
  universally. `POST /api/chat/{id}/resolve` mints a NEW turn id via `turns.spawn()`, so for
  the default `needs_approval` level a propose call and its later confirmed apply land in
  DIFFERENT turns and get DIFFERENT pool keys — each still spawns its own fresh child,
  exactly as before this change. Only a tool actually configured to `always_allow` fires
  once, inside one turn, with no resolve boundary to cross, so only that tool's sequential
  calls within a turn see the continuous-session benefit this ADR was written for.
  `tests/test_mcp_turn_connection_reuse.py::test_apply_confirmed_reuses_the_turn_connection_too`
  demonstrates the pooling mechanism itself but does so under one synthetic `turn_id`
  spanning propose and apply — see that test's own note for why that shape does not occur on
  the real HTTP path.
- A stdio server that is expensive to cold-start now pays that cost once per turn instead of
  once per call — a straightforward latency win alongside the correctness fix.
- Out of scope, by design: idle-timeout / reconnect-after-long-silence semantics for a
  connection that would span multiple, unrelated turns. Revisit only if a real workload
  needs cross-turn session affinity (e.g. an agent explicitly resuming a long Playwright
  session across turns) — not speculatively.

## Agent provenance

- **Decided by:** implementing Claude agent, on the owner's fully-diagnosed bug report (the
  Playwright-MCP navigate/screenshot repro, the `make_connection` docstring's own stated
  reuse contract, and a direct read of Codex's `connection_manager.rs` as the comparison
  client).
- **Lane / branch:** isolated worktree `agent-a861bccb6202de8f2`.
- **Driving artifact:** none pre-existing — this is the design record for the fix itself,
  written in the same change as the code per this repo's Definition of Done ("significant
  decisions get an ADR in the same change").
