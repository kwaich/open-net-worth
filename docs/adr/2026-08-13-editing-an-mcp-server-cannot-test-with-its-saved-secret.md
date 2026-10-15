---
title: Editing an MCP server cannot Test connect with its saved secret — the form says so instead of pretending
status: Accepted
date: 2026-08-13
deciders: implementing Claude agent (connectors frontend lane) + design handoff §7
---

# Editing an MCP server cannot Test connect with its saved secret — the form says so instead of pretending

## Context

Spec §3 requires the detail page's kebab item `Edit connection` to **reopen the setup drawer
pre-filled**, and spec §7 makes **Test connect the gate**: "an unreached server is never saved."
Closing gap G9 means holding both at once, on a server that already exists.

Three existing rules collide there, and all three are load-bearing:

1. **A saved secret is never returned to the browser.** `McpClientStore.public()` exposes
   `has_token` (a boolean) and `env_keys` (key NAMES only, because an env value may be the
   spawned server's own credential). The token itself and every env value are write-only at the
   API boundary.
2. **The probe endpoint is deliberately id-less.** `POST /api/mcp-clients/probe` takes a bare
   connection config and persists nothing on either path — that is exactly what lets the ADD
   flow reach a server before any record exists. It therefore has no server to look a stored
   secret up from, by construction rather than by omission.
3. **`env` is a whole-map replace with no "blank means keep" rule.** `token` has one (the store
   skips a blank); `env` does not.

So on an edit there is no arrangement in which "Test connect" exercises a credential the browser
is not allowed to see. The options were:

* **(a) Force re-entry.** Require the secret before Test connect is enabled. Verifies exactly
  what gets saved — but a user who wants to fix a typo in a URL and no longer has the API key to
  hand is locked out of their own server entirely, with removing and re-adding as the only way
  out. It converts a small edit into a credential hunt.
* **(b) Echo a masked value and send it back.** Requires the secret to reach the browser. Defeats
  rule 1 outright.
* **(c) Save first, then test.** Defeats rule 2's whole purpose, and can break a working server.
* **(d) Blank means keep — and SAY what that costs.** The pattern the outgoing MCP form already
  used for `token`, and the one the store itself implements.

## Decision

**(d).** On an edit, every secret field starts empty and stays empty unless the user retypes it:

* **API key** — placeholder `•••••••• saved`, with the note *"The saved key is never shown.
  Leave this blank to keep it — Test connect then runs WITHOUT it, so re-enter the key if this
  server needs one to answer."* A blank field is omitted from the `PATCH`, so the store keeps
  what it has.
* **Environment variables** — the box starts EMPTY and the saved keys are named beside it
  (*"Already set: VENDOR_API_KEY, PORT — values are never shown"*). An untouched box omits `env`
  from the `PATCH` entirely; a rewritten one replaces the whole set. It is deliberately NOT
  pre-seeded with `KEY=` lines: with a whole-map replace on the other end, adding one new
  variable to a pre-seeded box would blank every existing value. **The outgoing form did exactly
  that** — it built `{KEY: ''}` from `env_keys` and PATCHed it, under a comment claiming a blank
  value meant "keep", which was never true of `env`.

Test connect stays REQUIRED on an edit: an edit that cannot reach the server is not saved, same
as an add.

## Consequences

* Editing a server whose credential the endpoint actually requires means retyping that
  credential — which is the common case anyway, since rotating a key is the main reason to open
  this form (see PARITY D16).
* Editing only the URL/args of such a server surfaces the server's own auth error inline on the
  Test connect step. That is a real failure being reported accurately, not a bug: we genuinely
  have not verified that config end to end. The help text states the cause up front so the error
  is legible rather than mysterious.
* **What would remove the trade-off entirely is a backend change**, and it belongs to whoever
  owns that surface: an optional `server_id` on `McpServerProbe` that resolves the stored
  secret server-side for fields the request left blank. It keeps the secret out of the browser
  AND tests the real credential. Deliberately not built here — this lane was scoped to the
  frontend, and a probe that reads stored credentials needs its own `/security-review` (it turns
  an unauthenticated-config endpoint into one that can exercise a saved credential against an
  arbitrary attacker-supplied URL — i.e. a credential-exfiltration primitive if the URL is not
  pinned to the saved one).

## Also decided here (same reasoning)

**Server ID and Transport are FIXED on an edit**, and shown as such rather than offered and
ignored. `McpClientStore.update`'s `patchable` set excludes both: the id namespaces every tool
as `mcp__<id>__<tool>` and keys every `connector_tool_permissions` row, so changing it would
orphan all of them; transport is fixed at create by the same store. The paste-a-snippet pill is
hidden on an edit for the same reason — parsing a snippet switches transport, so leaving it
would be a control that silently does nothing.
