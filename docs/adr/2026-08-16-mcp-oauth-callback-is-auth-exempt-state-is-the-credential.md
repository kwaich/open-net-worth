---
title: The MCP OAuth callback route is auth-exempt — the single-use `state` is the credential
status: Accepted
date: 2026-08-16
deciders: implementing Claude agent (mcp-oauth-p3-flow lane), design ratified in RFC 2026-08-14-mcp-oauth §4.3
---

# The MCP OAuth callback route is auth-exempt — the single-use `state` is the credential

## Context

RFC `2026-08-14-mcp-oauth` adds `GET /api/mcp-oauth/callback` — the browser redirect an OAuth
authorization server sends the user back to after they consent to letting Open Net Worth reach
their Mercury/Avalara/Expensify/Twelve Data (or any DCR-capable) MCP server. Every other route in
this app is gated by the single app-wide `require_auth` dependency (`api/_deps.py`); this is only
the second route ever added to its `_EXEMPT` set, after `GET /api/shortcuts/download`.

The session cookie (`api/auth.py`) is `samesite="lax"`, so a top-level redirect back from a
third-party site *usually* carries it. "Usually" is not good enough here: the vendor consent
screen can take minutes (Avalara's is a full IdentityServer login), the session may expire
mid-flow, and the user may finish the sign-in on a different browser or device than the one that
started it (the QR-scan-on-a-phone case the shortcuts precedent already solved). Requiring a live
session at the callback means an expired session burns the authorization code — a one-time,
already-consented credential — with no recovery except starting the whole vendor consent flow
over.

## Decision

We will keep `GET /api/mcp-oauth/callback` in `api/_deps._EXEMPT`, and treat the single-use
`state` value as the flow's actual credential — mirroring `services/shortcuts/tickets.py`'s
"a short-lived ticket is the credential" precedent exactly.

That is only safe because every one of these holds, and they are enforced in code, not merely
asserted here:

- `state` is 256-bit, server-minted (`secrets.token_urlsafe(32)`, `oauth_pending.stage`) —
  unguessable.
- Single-use: `oauth_pending.pop(state)` **deletes** the staged flow on the first successful read.
  A replayed `state` gets nothing (`tests/test_mcp_oauth_pending.py::test_pop_is_single_use_a_replay_gets_nothing`,
  `tests/test_mcp_oauth_flow.py::test_complete_state_is_single_use_a_replay_fails`).
- TTL-bounded: 10 minutes (`oauth_pending.TTL_SECONDS`), after which `pop` returns `None`
  regardless of correctness.
- Bound to everything the exchange needs at mint time — `code_verifier`, `redirect_uri`,
  `expected_issuer`, `resource`, the resolved client — so a stolen `state` alone cannot complete a
  different flow's exchange.
- The route accepts **GET only** (`test_callback_only_accepts_get`), does exactly one thing
  (complete the one staged flow named by `state`), echoes no caller-supplied text into its HTML
  response, and answers an unknown/expired `state` with the exact same fixed page a success gets —
  no oracle (`test_callback_with_a_bogus_state_is_a_generic_page_no_stack_no_oracle`,
  `test_callback_page_is_static_regardless_of_query_params`).
- PKCE (`code_verifier`, never transmitted, S256 only) is the second independent layer: even a
  leaked authorization `code` is useless to an attacker without it.

## Consequences

### Positive
- A slow or interrupted human sign-in (minutes-long vendor consent, a session that expired while
  waiting, a different device finishing the flow) still completes — the failure mode the
  alternative would introduce (an authorization code burned by our own 401) never exists.
- The pattern is not novel to this codebase — it is the exact shape `/api/shortcuts/download`
  already established and got reviewed for, extended to a second, narrower case.

### Negative
- A second permanent hole in the app-wide auth gate. Every future reviewer of `_deps._EXEMPT` has
  to reason about two exemptions instead of one, and a security review is explicitly required on
  this line (RFC §9 item 4).
- The callback route becomes a very small but real CPU sink reachable by anyone who can guess a
  URL shape (not a value) — bounded by rate limiting being unnecessary at 256 bits of entropy per
  attempt, per the RFC's own review-scope note, but still worth a reviewer's second look.

### Follow-ups
- Formal `/security-review` gate before the frontend (Phase 5) makes this route reachable from a
  screen, per the RFC's own rollout plan.

## Alternatives considered

- **Keep `require_auth` on the callback** — rejected. Buys defence-in-depth against a leaked
  `state` (a Referer header, browser history, a proxy log) at the cost of a common and
  unrecoverable failure mode (consent granted, code burned, 401) — PKCE already makes a leaked
  `state`/code pair alone insufficient, so the marginal defence is small next to the real cost.
- **Full-page redirect instead of exempting the API route and completing in a popup** — orthogonal
  to this decision (a frontend/UX question, Phase 5), not a substitute for it: the callback still
  has to be *reachable* by an unauthenticated browser however the client renders it.

## Agent provenance

- **Decided by:** implementing Claude agent, `lane/mcp-oauth-p3-flow` (backend orchestration
  phase of the MCP OAuth feature).
- **Lane / branch:** `.claude/worktrees/mcp-oauth-p3`, branch `lane/mcp-oauth-p3-flow`.
- **Alternatives weighed:** auth-gated callback (rejected — burns a consented code on session
  expiry, RFC §4.3's own rejected-alternative writeup).
- **Driving artifacts:** `.claude/rfcs/2026-08-14-mcp-oauth.md` §4.3 (the four hard questions),
  §9 item 4 (security-review scope); precedent `services/shortcuts/tickets.py` +
  `docs/adr` review of `/api/shortcuts/download`'s own exemption.
- **Why this way (one line):** the callback's only correctness requirement is "complete the ONE
  flow this unguessable, single-use, TTL-bounded value names" — a live session is not what
  authorizes that, the `state` already does.
