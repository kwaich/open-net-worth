---
title: External-MCP stdio connections persist for the app's lifetime, not a turn's
status: Accepted
date: 2026-08-22
deciders: owner + implementing Claude agent
---

# External-MCP stdio connections persist for the app's lifetime, not a turn's

## Context

Three shapes were tried in order, each fixing a real bug the previous one left behind:

1. **Per-call (original).** `McpClientManager._fire` spawned a brand-new stdio child on
   every single tool call. Broke Playwright MCP (`browser_navigate` then
   `browser_screenshot` as two calls got a blank, memory-less browser — its open page/context
   lives on the client session, i.e. the child process's lifetime).
2. **Per-turn** (ADR `2026-08-22-external-mcp-stdio-connections-reused-per-turn`, now
   superseded). One live child per `(turn_id, server_id)`, closed the instant the turn
   ended. Fixed Playwright's within-one-message case.
3. **Per-app-process (this ADR).** Still not enough: `interactive-brokers-mcp`, a real
   IBKR trading MCP server, needs its connection to survive ACROSS MULTIPLE CHAT MESSAGES.
   The owner authenticated via a browser flow, then in a LATER message the SAME server
   reported "Authentication required" again — reproduced live, repeatedly. Root cause:
   IBKR's gateway session needs continuous keep-alive activity to stay authenticated (the
   package runs an internal timer once authenticated), which only survives as long as the
   SAME Node child process stays alive. `POST /api/chat/{id}/resolve` mints a NEW turn_id
   via `turns.spawn()` for every message, so even the correctness fix in #2 tore the
   child down between messages — a fresh, unauthenticated child every time, and by then the
   real IBKR gateway session had often already expired from inactivity too. Claude Desktop,
   by contrast, keeps ONE persistent connection open for the WHOLE conversation (confirmed:
   zero auth-drop issues observed there across the same testing session).

A chat SESSION (not just a turn) was considered as the intermediate scope and explicitly
rejected before implementation: a session in this app persists indefinitely (the user can
return to a months-old conversation), so "close when the session ends" has no natural
trigger, and IBKR's actual need — one browser-authenticated gateway session staying alive —
has nothing to do with which conversation happens to be asking. Two different conversations
about the same brokerage account should see the SAME authenticated connection, not each
pay their own re-auth cost. The owner's own framing settled this directly: match Claude
Desktop, where the connection lives for as long as the app is open, full stop.

## Decision

### Scope: one connection per (server), for the life of the backend process

`McpClientManager` pools **at most one live `StdioMcpClientConnection` per `server_id`**,
established eagerly at boot for every configured+enabled stdio server, reused by EVERY
turn/session that calls that server afterward, and closed only on: the connection dying
(detected + transparently reconnected on next use), the server's config changing
(command/args/enabled toggled via the Connectors UI — closed and reconnected with the new
config), or the backend process shutting down.

No turn-id, no session-id, no per-caller bookkeeping at all: `self._server_conns: Dict[str,
StdioMcpClientConnection]`, keyed by `server_id` alone. This also deletes the tombstone
machinery the per-turn version needed (`_ended_turns`/`_register_or_discard`'s
check-and-register race guard) — that existed specifically to stop a straggler spawn from
registering under a turn_id that had already ended and would never be looked at again. An
app-lifetime connection has no such "already ended" moment during normal operation (only
process shutdown, handled separately below), so there is nothing to guard against.

### Eager connect at boot, non-blocking

`McpClientManager.connect_enabled()` (already existed, already called from `main.py`'s
lifespan) now does two different things depending on transport:

- **stdio**: spawn the child, run `initialize()`, cache it as the pooled connection, then
  run `tools/list` on that SAME connection and record discovery — one spawn serves both
  "keep this warm" and "populate the tools cache," where the per-turn version's discovery
  path spawned a second, throwaway child for the identical handshake. (`test_connect`/the
  add-flow's `probe` deliberately still use the OLD, ephemeral, unpooled path — see "Manual
  Test Connect stays ephemeral" below.)
- **http**: unchanged — `_discover()`'s existing ephemeral discovery. HTTP is still out of
  scope for pooling (see the superseded ADR's reasoning, unchanged: `_call_tool_blocking`
  never calls `initialize()` on the HTTP transport, so there is no `Mcp-Session-Id` a
  persistent connection would preserve).

**Concurrency, not sequencing, is what makes this non-blocking.** `connect_enabled()`
itself stays a single, fully synchronous, blocking call — deliberately: roughly fifteen
existing unit tests call it directly and assert on `discovered_tools`/`last_status`
immediately afterward, with no event loop running in most of them. Rewriting it to
fire-and-forget internally (an `asyncio.create_task` per server) would have broken every
one of them and required a running loop `connect_enabled()` cannot assume. Instead it now
runs one `threading.Thread` per enabled server and joins them all before returning — a slow
or hung server no longer delays another server's connect (true concurrency, not just
try/except isolation, which the code already had), while the method's outward contract
(block until every server's connect attempt has resolved, one way or another) is
unchanged bit for bit.

**Boot itself never blocks on this at all.** `main.py`'s lifespan used to `await
asyncio.to_thread(_mcp_client_manager.connect_enabled)` — the whole boot sequence waited
for every server's handshake before continuing to `yield`. It now wraps that same call in
`asyncio.create_task(asyncio.to_thread(...))` and does NOT await it: the task reference is
kept (so it isn't garbage-collected mid-flight) and given a bounded wait at shutdown, but
`yield` happens immediately. A slow/unreachable/misconfigured server can now take as long
as it likes (bounded only by the existing per-call `STDIO_CALL_TIMEOUT_SECONDS`) without
delaying the app becoming ready to serve requests.

### Lazy connect-on-first-use is the fallback, not a separate mechanism

A server added or enabled AFTER boot (via the Connectors UI, or one that failed at boot and
was fixed and re-enabled later) does not need an app restart: `_call_via_server_connection`
— the same function every real tool call goes through — spawns+registers a connection on
demand if none is pooled yet, exactly the eager-connect codepath minus the "also list_tools
and record discovery" tail. One `_get_or_spawn_connection_locked` helper serves both boot
eager-connect and this on-demand path, so there is no drift between them.

### Config-change reconnect: evict, then best-effort reconnect

`services.connectors.remove_mcp_server` and `api/mcp_client_api.py`'s
`create_mcp_client`/`update_mcp_client` now call
`McpClientManager.reconcile_pooled_connection(server_id)`:

- always evicts (closes) any currently-pooled connection for that server_id — a server
  whose command/args changed, or that was just disabled, must never keep serving calls
  against the OLD spawned process's stale command/credentials;
- then, if the CURRENT config (re-read from the store, not the caller's stale view) is
  still enabled + stdio, immediately attempts a fresh connect with the new config — so an
  operator re-enabling or repointing a server sees it connected right away rather than
  waiting for the next real tool call to pay that cost. `update_mcp_client` only calls this
  when the endpoint actually changed OR `enabled` was toggled (mirrors the existing
  `_endpoint_changed` gate that decides whether to withdraw `allow` grants) — a pure rename
  does not force a needless respawn.
- On delete, the server row is already gone by the time this runs, so
  `reconcile_pooled_connection` finds nothing to reconnect and only evicts — exactly right.

This reuses `_boot_connect_one` (the same spawn+initialize+list_tools+record_discovery
sequence boot uses), run synchronously inside the REST handler — the SAME blocking
contract `create_mcp_client` already has today via its existing `test_connect(...)` call for
a newly-created enabled server, so this is not a new kind of latency the CRUD routes didn't
already have.

### Concurrency: one shared connection, genuinely concurrent callers, a serializing lock

This is the one property that changed in kind, not just in scope, from the per-turn design.
Under per-turn pooling, at most one caller was ever active against a given
`(turn_id, server_id)` connection at a time — CONC-H3
(`ai/assistant.py::_get_session_lock`) serializes concurrent turns on the SAME session, and
a turn's own tool-dispatch loop already runs its calls one at a time, so nothing but that
one turn's sequential calls ever touched that connection concurrently.

An app-lifetime connection has no such guarantee: `ai/turns.py::spawn()` registers each
turn as an independent `asyncio.Task` with no cross-session synchronization at all — two
DIFFERENT sessions' turns run fully concurrently, and both can legitimately want to call the
SAME external MCP server at the same time (verified by reading `turns.py`: `_get_session_lock`
is keyed per session_id, `spawn`/`_register` have no session-to-session interaction).

`StdioMcpClientConnection`/`_StdioSession` (`backend/mcp/stdio_client.py`) is **not**
safe for concurrent use by design: `rpc()` writes one line to `proc.stdin` and then loops
reading `proc.stdout` until a reply with the matching `id` arrives, skipping unmatched
lines. That "skip what isn't mine" tolerance reads like partial multiplexing support, but it
is not: two threads calling `stdout.read1()` concurrently on the same buffered stream can
each receive an arbitrary, unpredictable slice of bytes, splitting or interleaving JSON-RPC
frames between two readers with no way for either to reconstruct a whole line — and two
threads writing to `proc.stdin` concurrently have no ordering guarantee either. This is a
single-logical-request-at-a-time transport, full stop; the manager, not the transport, has
to enforce that.

So `McpClientManager` gained a `threading.Lock` PER SERVER
(`self._server_rpc_locks: Dict[str, threading.Lock]`, created lazily), held for the
FULL duration of a call: connect-or-reuse decision, the dead-connection check, and the
actual `call_tool` RPC are all one critical section per server. A second, concurrent caller
for the SAME server blocks (queues) behind the first rather than racing it on the shared
pipes; callers for DIFFERENT servers never contend with each other (separate locks). This is
a deliberate choice to serialize rather than build real request pipelining (matching
requests to concurrent futures keyed by `id`, reading in a dedicated background thread) —
that would be a materially bigger transport rewrite for a self-hosted app talking to a
handful of local MCP servers, where "the second concurrent call to the same server waits its
turn" is an entirely acceptable cost. `_get_or_spawn_connection_locked` and
`_close_locked` are BOTH internal helpers that assume the caller already holds this lock;
nothing else touches `self._server_conns[server_id]` outside it.

## Security: the permission/confirm-gate model is unchanged

Exactly the same argument as the superseded ADR, restated because the sharing got broader:
`call_external_tool` resolves `tool_permissions.get_tool_permission(...)` and only reaches
`_fire` (where the shared connection lives) on `always_allow`; `needs_approval` always parks
a proposal first, and `apply_confirmed` re-resolves the level again before firing. Connection
reuse/sharing lives entirely inside `_fire`'s blocking half — strictly downstream of every
one of those checks, for every caller, regardless of which session or turn it belongs to.
Nothing about a shared, locked, app-lifetime connection changes when or whether that gate
runs: it is consulted fresh on every `call_external_tool`/`apply_confirmed` invocation.
Regression-tested directly, now across two SEPARATE calls simulating two different
turns/sessions sharing the one pooled connection rather than two calls within one turn (the
old test's shape) —
`tests/test_mcp_app_lifetime_connection.py::test_permission_gate_still_runs_independently_per_call_on_a_shared_connection`.

**What DOES change, worth restating and widening**: the "earlier ungated calls in this turn
may have already shaped the session state a later confirm card runs against" caveat from the
superseded ADR now applies ACROSS THE WHOLE APP LIFETIME, not just within one turn. An
`always_allow` tool call from ANY session, at ANY point since boot, can have shaped what the
shared connection's server-side session looks like by the time a `needs_approval` call from
a DIFFERENT session later parks a card for the same server. This is the direct, intended
consequence of matching Claude Desktop's model (one persistent connection for the whole app,
not reset per conversation) — not a new hole this change opens carelessly, but it is a wider
blast radius than "within one turn" and is worth an operator's explicit awareness.

## Manual "Test Connect" / probe stay ephemeral, deliberately unchanged

`McpClientManager.test_connect`/`_discover` (the human-clicked "Refresh tools list") and
`probe` (the add-flow's pre-save "Test connect", which is guaranteed to have no
`server_id` yet) are UNTOUCHED — still their own one-off, unpooled connection, exactly as
before this ADR. Considered folding them into the shared pool and rejected: a user
deliberately clicking "Test Connect" is asking "can I reach this endpoint right now," which
should not be entangled with (and should not disturb) whatever real, possibly-mid-request
state the live pooled connection is carrying for actual tool calls. The pooled connection's
own dead-connection detection already self-heals on the next real call regardless of what a
manual test did or didn't do.

## Consequences

- Fixes the reported IBKR case: the child process (and the gateway session's keep-alive
  timer running inside it) now survives across every chat message, not just within one,
  matching the owner's Claude Desktop comparison.
- A stdio server that is expensive to cold-start pays that cost ONCE per app boot (or once
  per config change), not once per turn — a bigger latency win than the per-turn version,
  and now genuinely once, not once-per-conversation.
- The concurrency property most worth remembering: a slow/hung call to a server now blocks
  every OTHER concurrent caller of that SAME server (never a different server) until it
  finishes or its own timeout fires — a real, accepted trade-off for a self-hosted app,
  not a purely-free upgrade from the per-turn design's implicit "callers never overlap."
- No idle-timeout reaper, no session/turn tombstone set, no straggler-registration race to
  guard — all deleted, not carried forward, because none of them describe anything true of
  an app-lifetime connection's actual lifecycle.
- Out of scope, unchanged from the superseded ADR: real request pipelining on one
  connection (would remove the serializing-lock cost above); HTTP session-affinity pooling
  (still no `initialize()` call on that transport at all).

## Amendment (2026-08-22, security review H1/H2/H3)

A same-night security review of this design found three HIGH-severity gaps, all fixed in the
same review cycle (no design change to the scope/decision above — these are hardening fixes
to the implementation):

- **H1 — the persistent connection could desync and hang for up to an hour.** The stdio
  reader (`stdio_client._StdioSession._read_capped_line`) inherited a stateless-per-call
  assumption ("a well-behaved server sends exactly one reply line and never pipelines") that
  this ADR's own persistence directly invalidates: a real server CAN write an async
  `notifications/*` line and the actual reply in one flush, landing in the same `read1()`
  chunk. The reader dropped bytes after the first newline, silently discarding the real
  reply and hanging the read loop until the watchdog fired — while holding this ADR's
  per-server lock the whole time, stalling every other caller of that server. Fixed with a
  persistent per-session receive buffer (`self._rxbuf`) that keeps and later consumes those
  bytes instead of dropping them. Regression test:
  `tests/test_mcp_stdio_client.py::test_a_notification_pipelined_with_the_reply_survives_the_persistent_connection`.
- **H2 — the per-server lock plus a same-night 3600s call-timeout bump combined into an
  unbounded stall, with no way for an admin action to win.** `STDIO_CALL_TIMEOUT_SECONDS` had
  been blanket-bumped from 20s to 3600s to get one server's cold package install through —
  correct for that ONE spawn, wrong applied to every ordinary call on an already-warm pooled
  connection (contrary to "Boot itself never blocks on this at all" above, which cited the
  original 20s figure). Split into two budgets: `STDIO_CALL_TIMEOUT_SECONDS` (restored to
  20s, steady-state) and `STDIO_CONNECT_TIMEOUT_SECONDS` (300s, the pool's first
  spawn+`initialize()` only — passed as a per-call override, never the connection's own
  `Deadline`, since a `Deadline` decays and this connection can live for the app's whole
  uptime). Separately, this ADR's "held for the FULL duration of a call" lock (§ Concurrency
  above) was an UNBOUNDED `with lock:` on both the ordinary call path and the three eviction
  paths (`reconcile_pooled_connection`, `close_all_pooled_connections`,
  `_boot_connect_one`) — meaning a wedged server could also block the operator's own
  disable/delete/shutdown actions on that same server, contradicting `close_all_pooled_
  connections`' own "acquires instantly" claim. Now bounded everywhere:
  `_call_via_server_connection` uses `lock.acquire(timeout=5s)`, returning a fenced "server is
  busy" tool-error on a miss instead of queuing indefinitely (revising the "blocks every
  OTHER concurrent caller... until it finishes" line under Consequences above — that is no
  longer true past ~5s); the three eviction paths go through a new `_evict_now`, which tries
  the lock briefly (3s) then force-terminates the child directly if that fails, so an admin
  action always wins. `_boot_connect_one` also now evicts a reused connection that fails
  `list_tools()` during boot/reconcile, matching the ordinary call path's own evict-on-error.
- **H3 — rotating a stdio server's `env` (credentials) didn't evict the pooled child.** The
  PATCH route's reconcile trigger compared `url`/`command`/`args`/`enabled` but not `env` —
  exactly where a stdio server's own API keys live — so an operator rotating a compromised
  key got a false "success" while the already-pooled child kept running on the OLD env until
  the backend restarted. Fixed two ways: `api/mcp_client_api.py` gained a separate
  `_spawn_signature_changed` check (deliberately NOT folded into `_endpoint_changed`, which
  also drives permission-revocation semantics that must not fire on a pure credential
  rotation) that now also triggers `reconcile_pooled_connection`; and, as a structural
  backstop, `McpClientManager._get_or_spawn_connection_locked` now records the `(command,
  args, env)` signature a pooled child was actually spawned with and compares it against the
  server's CURRENT config on every connect-or-reuse, evicting on a mismatch even if a future
  caller of this API forgets to reconcile explicitly.

## Agent provenance

- **Decided by:** implementing Claude agent, on the owner's IBKR repro (a
  repeated "Authentication required" regression across chat messages, root-caused to the
  per-turn connection's process lifetime vs. the vendor package's own keep-alive-dependent
  gateway session) and an explicit owner correction mid-implementation: an intermediate
  per-chat-session design was scoped, partially investigated, and then deliberately dropped
  in favor of this app-lifetime scope before any code was written against it, on the
  owner's own instruction to match Claude Desktop's observed behavior exactly.
- **Driving artifact:** none pre-existing — this is the design record for the fix itself,
  written in the same change as the code per this repo's Definition of Done.
