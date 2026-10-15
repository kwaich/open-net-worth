---
title: OAuth client re-registration happens only on a human-initiated `begin` — never from a background refresh
status: Accepted
date: 2026-08-16
deciders: implementing Claude agent (mcp-oauth-p3-flow lane), design ratified in RFC 2026-08-14-mcp-oauth §4.5
---

# OAuth client re-registration happens only on a human-initiated `begin` — never from a background refresh

## Context

`ensure_fresh_token` (`backend/mcp/oauth_flow.py`) runs UNATTENDED — before every autonomous
external-MCP tool dispatch (`client_manager._fire`), before every discovery handshake
(`client_manager._discover`, including the scheduled `connect_enabled` boot sweep), and reactively
inside `mcp/client.py::_rpc` on a live 401. If a token refresh fails because the authorization
server has revoked or rotated our DCR client registration (`error: "invalid_client"`), the naive
recovery — "re-run DCR and try again" — is reachable from every one of those unattended call
sites, with no human ever in the loop.

## Decision

We will make `invalid_client` handling stop, not recover, on every background path:

`ensure_fresh_token` clears BOTH the registration and the token set (`store.clear_oauth`, which
zeroes `client_id`/`client_secret`/`access_token`/`refresh_token` together — one block, one write)
and records `last_status="auth_error"` with a message naming reconnection. It does **not** call
`oauth.register_client` anywhere in that path — `tests/test_mcp_oauth_flow.py::test_ensure_fresh_token_never_re_registers_on_a_background_path`
pins this directly (monkeypatches `oauth.register_client` to record every call, then asserts zero
calls after a simulated `invalid_client` refresh failure).

Re-registration is reachable from exactly one place: `oauth_flow.begin`, which only runs on
`POST /api/mcp-clients/oauth/begin` — an authenticated, human-initiated request. Recovery from a
dead sign-in is one explicit action ("Reconnect" on the connector's detail page → `begin` with
`server_id` → the same consent popup); registration is reused if a still-valid one exists for the
issuer, and re-minted only as part of that same human-initiated call.

## Consequences

### Positive
- A hostile or compromised authorization server — or anything able to forge its `invalid_client`
  response — cannot make this backend mint new DCR client registrations unattended, indefinitely,
  by simply keeping every refresh attempt failing. The blast radius of a bad AS response is capped
  at "this connector goes to `auth_error` and stops working" — visible, not silently self-healing
  into more registrations.
- The dead-sign-in state is not a dead end: `last_status="auth_error"` is the *first* real writer
  of that documented-but-previously-unused status value (RFC §4.7), already wired end to end to
  the Connectors status surface (`services/connectors.py::_status`, the frontend's
  `ERROR_STATUSES`/`STATUS_TONE` maps) — so the failure is visible and actionable, not silent.

### Negative
- A revoked registration means the connector is fully down until a human clicks Reconnect — there
  is no automatic self-healing path, even for a benign cause (e.g. the vendor rotated
  registrations as routine maintenance). This is an accepted trade-off, not an oversight: the
  alternative (auto-re-register) is exactly the unattended-forever risk this decision closes.

### Follow-ups
- None — this mirrors an existing pattern already accepted elsewhere in the codebase
  (`apply_confirmed` re-reading the permission level before firing, rather than trusting a
  stale decision) and needs no further work to be complete.

## Alternatives considered

- **Auto-re-register on `invalid_client` and retry the refresh once** — rejected as the RFC's own
  named security requirement (§4.5): it lets any authorization server (or anything able to forge
  its response) make this backend mint new client registrations unattended, forever, with no human
  ever seeing it. The cost of NOT doing it is one manual Reconnect click; the cost of doing it is
  an unbounded unattended registration primitive.
- **Auto-re-register only after N consecutive failures, rate-limited** — rejected as a weaker
  version of the same hole: it still eventually re-registers unattended, just more slowly: the
  security property RFC §4.5 wants is "never without a human," not "rarely without a human."

## Agent provenance

- **Decided by:** implementing Claude agent, `lane/mcp-oauth-p3-flow`.
- **Lane / branch:** `.claude/worktrees/mcp-oauth-p3`, branch `lane/mcp-oauth-p3-flow`.
- **Alternatives weighed:** unattended auto-re-register (immediate or debounced) — both rejected
  in RFC §4.5 and reaffirmed here.
- **Driving artifacts:** `.claude/rfcs/2026-08-14-mcp-oauth.md` §4.5, §4.7 ("Failure at 3am, on
  the scheduler with nobody watching"); `tests/test_mcp_oauth_flow.py`'s
  `test_ensure_fresh_token_invalid_client_clears_registration_and_marks_auth_error` +
  `test_ensure_fresh_token_never_re_registers_on_a_background_path`.
- **Why this way (one line):** every path that can hit `invalid_client` runs with nobody watching,
  so the only safe response is to stop and wait for a human, exactly like the codebase's existing
  re-read-before-fire pattern for a confirm gate.
