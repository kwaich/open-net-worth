---
title: The discovery gate validates external-MCP calls against the discovered schema
status: Accepted
date: 2026-10-03
deciders: Repo owner, Claude
---

# The discovery gate validates external-MCP calls against the discovered schema

## Context

The discovery gate always bounced an external-MCP tool called before its definition was loaded.
A weak model read the "NOT EXECUTED" bounce of `fetch_emails` as "no emails" and an unattended
automation closed as complete. Internal reads with valid args already run on the first call.

## Decision

An external tool runs on its first call when (1) the server's handshake advertised an
`inputSchema` and the emitted args validate against it with a real, strict JSON-schema validator
(`registry.external_args_error`: `required`, enums, `additionalProperties`, exact types);
(2) OUR name allowlist classifies it read (`mcp_external.is_provably_read_only`). Server
annotations are untrusted: `readOnlyHint: false` / `destructiveHint: true` veto, `readOnlyHint: true`
grants nothing. Otherwise it bounces as before (`bounced_external` unchecked, `bounced_write`,
`bounced_invalid_args`). Passing the gate only falls through to the normal external dispatch, so
`tool_permissions` (blocked / needs_approval staging / always_allow) governs identically to a
retry; the gate never fires a call itself. Telemetry gains `executed_external`.

## Consequences

An `always_allow` read-named external tool now fires one round trip sooner, with args the model
chose before seeing the tool's description. Bounced-and-never-retried runs are not yet flagged.
