---
title: An MCP server's tool cache is DISCOVERED by us, never supplied by the caller — so create runs its own handshake
status: Accepted
date: 2026-08-14
deciders: implementing Claude agent (connectors-redesign lane, QA defect D1/D4)
---

# An MCP server's tool cache is DISCOVERED by us, never supplied by the caller — so create runs its own handshake

## Context

QA reproduced this against the running app (defect **D1**, merge blocker):

```
POST /api/mcp-clients/probe   → ok, tools = [get_quote, list_orders, place_order, cancel_order]
POST /api/mcp-clients         → 200
PUT  /api/connectors/mcp%3Arepro/permissions/place_order {"level":"block"} → 200
GET  /api/connectors          → mcp:repro  tools: []      ← the gap
GET  .../permissions          → {"place_order": "block"}  ← stored, but invisible
```

The add flow's step 2 probes the endpoint and holds its `tools[]`; step 4 renders the permission
ladder over that list and writes the levels the user picks. But `POST /api/mcp-clients` only ever
saved the config — `discovered_tools` stayed `[]` until something else ran a handshake. So the
detail page rendered *"This connector exposes no tools to Lucius yet — nothing to permission. Use
Refresh tools list once the server is reachable"* about a server that was reachable, whose levels
were already stored against tool names nothing was rendering.

Three separate harms, in increasing order of severity: the user's step-4 choices appear lost; the
copy states the wrong reason; and the recovery a user reaches for — remove and re-add — is the one
action that also **drops** those levels (`delete_mcp_client` clears the connector's permissions by
design).

Two candidate fixes:

* **(a) Accept the probe's list in the create body.** One round trip, no second connection. The
  client already has the list.
* **(b) Discover server-side on create.** Costs a second handshake; self-verifying.

## Decision

**(b).** `create_mcp_client` runs the same handshake `POST /api/mcp-clients/{id}/test` runs, on the
config **as saved**, whenever the server is created *enabled*.

**(a) was rejected as a lie the API cannot detect.** The tool cache is a record of what *we*
observed through a handshake, and `record_discovery` is its only writer — `McpClientStore.update`
already refuses to patch `discovered_tools` for exactly this reason, and `verification` is
deliberately absent from `McpServerCreate` so that no request body can mint a Verified badge for an
arbitrary endpoint. (a) pierces that: a caller-supplied list can name tools the endpoint does not
advertise, and — the concrete abuse — can mislabel a write tool as `read_only`, landing it under
the ladder's **Read** heading, where one group stamp grants it *Always allow*. A tool's read/write
grouping is display-only, but the group control is a real write, so a lie about the grouping is a
lie about what the user is granting. The endpoint is behind `require_auth` and unreachable by
Lucius, so this is not an escalation path for an outsider; it is a way for the app to store, and
then act on, a claim it never verified. The cost of not doing it is one HTTP round trip.

There is also a plain correctness gain: (b) caches what the endpoint answers **for the credential
and argv we actually saved**, not what a possibly-different config answered a moment earlier.

A third option — have the probe stash its result under a short-lived server-side handle that create
redeems — was considered. It preserves the same invariant and skips the second connection, but it
adds expiring state, a config-fingerprint check (else you probe server A and create server B with
A's tools), and a silent-regression failure mode when the handle is lost. It buys one round trip
for real machinery, on a path a human runs once per server.

Three narrower calls inside (b):

1. **Discovery runs only when the server is created ENABLED.** The dormant-until-enabled rule boot
   discovery (`connect_enabled`) already follows: a server saved switched off is offered to nobody,
   so we do not reach out to its endpoint — with its credential attached — on the user's behalf. Its
   ladder is empty and the empty-state copy is then *true*. The Claude-Desktop bulk import lands
   exactly there.
2. **A failed handshake does NOT undo the create.** The flow's gate is the probe — an unreached
   server never reaches this route, and `POST /api/mcp-clients/probe` still persists nothing on
   either path. Discarding a completed four-step add, permission choices included, because a second
   connection was refused would replace a cosmetic defect with a data-loss one. The failure is
   recorded (`last_status`/`last_error`) and the row reads **Error** instead of claiming health.
3. **`test_connect` now uses the INTERACTIVE deadline** (`probe_deadline` — 30s http / 60s stdio)
   rather than the per-call timeout. Without it, a cold `npx -y <package>` that cleared the probe's
   60s budget could fail create's 20s-per-call one, and the fix would manufacture a `connect_error`
   for a server the user had just watched connect. Unattended boot discovery keeps the tight bound.

**D4 fell out beside it**, in `services.connectors._mcp_views`. `status` was
`pub["last_status"] or _status(enabled, bool(url))`, which is two defects: it returned the MCP-only
`last_status` vocabulary (`ok`/`connect_error`/…) verbatim into a field documented to hold
`active`/`configured`/`error`/`not_configured` — so fixing D1 would have started shipping
`status: "ok"` on every healthy server — and its fallback tested `url`, which is empty for a stdio
server, so a *local* server read `not_configured` however reachable it was. It now reports
`_status(enabled, bool(endpoint), last_error=…)`: one vocabulary, and configured means it has an
endpoint (url for http, command line for stdio). The raw `last_status` still rides in the `tools`
capability config, which is where the UI reads it to tell an auth error from a connect error.

## Consequences

- Adding an enabled MCP server costs one extra handshake and takes as long as the probe did. The
  detail page needs no manual refresh, which is the point.
- `POST /api/mcp-clients` can now take seconds and touches the network. It is `def`, so FastAPI
  runs it in the threadpool; nothing blocks the event loop.
- A server created *disabled* and enabled later still has an empty cache until boot or a manual
  "Refresh tools list". Narrow (nothing in the UI creates a disabled server) and honest while it
  lasts, but it is the remaining gap in this defect's class. Closing it means discovery on the
  enable path, which lives in `services.connectors` — and `services` may not import `mcp`, so it
  needs a `core` seam. Deliberately out of scope here.
- The remove-confirmation copy ("Lucius loses **the 4 tools it advertises**") now reads correctly on
  the first render instead of degrading to "access to it".

## Agent provenance

Implemented on `feature/connectors-redesign` by the agent assigned QA defect D1, which arrived with
both candidate fixes named and an instruction to pick the one that cannot be lied to. The
store-level invariant that decided it (`update`'s refusal to patch `discovered_tools`;
`record_discovery` as sole writer) was already in the code and is quoted above rather than
invented here — the decision is mostly the recognition that (a) would have been the first write to
break it.
