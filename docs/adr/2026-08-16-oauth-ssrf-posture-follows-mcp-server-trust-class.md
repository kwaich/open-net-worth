---
title: The OAuth discovery chain's SSRF posture follows the MCP server's own trust class
status: Accepted
date: 2026-08-16
deciders: implementing Claude agent (mcp-oauth-p3-flow lane), design ratified in RFC 2026-08-14-mcp-oauth §9 item 2
---

# The OAuth discovery chain's SSRF posture follows the MCP server's own trust class

## Context

`backend/mcp/client.py` deliberately does **not** route its connection to a configured MCP server
through `core.net_guard`/`core.pinned_http` — `http://127.0.0.1:3000/mcp` is a first-class,
documented way to run a local MCP server, adding/probing a server is human-only, and nothing
model-reachable can choose the URL. That reasoning holds for the one URL a human typed in.

OAuth discovery (RFC `2026-08-14-mcp-oauth`) introduces a chain of URLs **the MCP server itself
chooses**: the `WWW-Authenticate` challenge's `resource_metadata`, the protected-resource
metadata's `authorization_servers[0]`, the authorization-server metadata's
`registration_endpoint`/`token_endpoint`/`revocation_endpoint`. A hostile or compromised MCP
server can put anything it wants in those documents. The "nothing model-reachable picks this URL"
reasoning that exempts the MCP connection itself does **not** transfer to a URL a *server* chose —
this is the sharpest new attack surface the RFC's security-review scope names (§9 item 2).

## Decision

We will make every OAuth-related URL **except** the original, user-configured MCP server URL
follow a trust-class rule keyed off that MCP server's own reachability, implemented in
`backend/mcp/oauth.py::_fetch`:

- **The MCP server resolves to a public address** (`net_guard.assert_public_url` on `mcp_url`
  succeeds) → every OAuth URL derived from that server's own documents must ALSO pass
  `assert_public_url`, and is fetched through `core.pinned_http.pinned_urlopen` (closes
  DNS-rebinding: `169.254.169.254`, a LAN host, and a low-TTL attacker domain answering public
  at validation time and private at connect time are all refused).
- **The MCP server is loopback/private** (`assert_public_url` on `mcp_url` fails) → every OAuth
  URL must resolve to the **exact same host** as the configured server URL — never a pivot to
  some OTHER internal address a hostile local server might name — and is fetched the SAME
  unguarded way the MCP connection itself already is. `pinned_urlopen` is structurally
  unreachable on this branch: it refuses a private target unconditionally regardless of
  same-host, so same-host loopback traffic goes through a bare, scheme-checked `urlopen` instead
  (`oauth._unguarded_urlopen`).
- Independent of either branch: a non-loopback OAuth URL must be `https` (`_fetch` rejects `http`
  outright unless the host is loopback).

Every discovery/DCR/token/revoke function in `oauth.py` funnels through `_fetch`
(`_fetch_json`/`_post_json`/`_post_form`) — there is exactly one place this rule is enforced, not
one per call site. The **first** call of the whole chain, the unauthenticated `initialize` POST
to the user-configured `mcp_url` itself, is the sole exception and goes through
`_unguarded_urlopen` directly — it is not server-chosen, it is the same URL the ordinary MCP
transport already reaches unguarded.

## Consequences

### Positive
- A hostile public MCP server cannot use its own `WWW-Authenticate`/metadata documents to make
  this backend reach an internal address — `assert_public_url` refuses it, and even a real public
  hostname with a rebinding DNS answer is closed by `pinned_urlopen`'s pin.
- A hostile *local* MCP server (the more permissive branch, by necessity — a legitimate local dev
  AS is itself loopback and would otherwise be refused outright by `assert_public_url`) is still
  bounded to the ONE host it was configured to point at; it cannot use a forged metadata document
  to make this backend probe an arbitrary OTHER address on the operator's LAN.
- Cross-origin authorization servers — the normal case for three of the four catalog vendors
  (Stripe → `access.stripe.com`, Avalara → `identity.avalara.com`, both different hosts than
  their MCP endpoint) — are unaffected; the rule keys off the MCP server's OWN public/private
  status, not host equality with it, in the public branch.
- Tested directly against the trust-class boundary itself (not just the discovery logic above
  it): `tests/test_mcp_oauth.py`'s `test_fetch_*` group proves each of the four branches —
  public+public-url pins, public+private-url refuses, private+same-host uses the unguarded path,
  private+different-host refuses — independent of any vendor-shape fixture.

### Negative
- A loopback/private MCP server's OAuth endpoints get materially weaker SSRF protection than a
  public one's (no `pinned_urlopen`, no DNS-rebind pin) — an accepted, structural consequence of
  `pinned_urlopen` refusing private targets outright, not an oversight. The same-host constraint
  is the only defence on that branch.
- One more security-sensitive branch point for a future maintainer to keep correct — a change to
  `oauth._fetch` is now a change to the whole feature's SSRF posture, not a per-caller concern.

### Follow-ups
- Named explicitly in scope for the formal `/security-review` gate (RFC §9 item 2) before the
  frontend phase makes any of this reachable from a screen.

## Alternatives considered

- **Route every OAuth URL through `pinned_urlopen` unconditionally** — rejected: it would make a
  perfectly legitimate loopback/private MCP server's OAuth flow (a local dev AS, or a self-hosted
  AS on the same LAN) structurally impossible, since `pinned_urlopen` refuses a private target
  regardless of context.
- **Trust every OAuth URL unconditionally, matching the MCP connection's own posture** —
  rejected: the MCP connection's own exemption rests on "nothing model-reachable picks this URL",
  which is true for a human-typed server URL and false for a document that server itself
  authored — extending the same trust would hand a hostile server a live SSRF primitive through
  its own `WWW-Authenticate` header.

## Amendment (2026-08-16, post-security-review)

The mechanism this ADR describes shipped with two CRITICAL gaps the RFC §9 item 2
`/security-review` gate found and a fix pass closed, before any of this became reachable from a
screen:

1. **The private-branch "exact same host" check was port-blind** — it compared `hostname` only,
   so a hostile loopback MCP server could point OAuth discovery at a *different port* on the same
   host, including this app's own bind port (`POST /api/reset`, reachable unattended via the
   background token-refresh path). Fixed by comparing the full `(scheme, host, port)` origin, plus
   an unconditional refusal of the app's own configured port whenever the target host is loopback.
2. **`_unguarded_urlopen`'s redirect handling never re-ran the trust-class check on a hop** — it
   used the stock `urllib` opener, which auto-follows a 3xx to *any* host. A fix landed manual,
   re-validated redirect handling on this branch, capped at 5 hops — and, as a side effect of
   building that, found the SAME bug in `pinned_urlopen`'s own (public-branch) redirect-disabling
   code: `OpenerDirector.add_handler` registers a handler in *two* places
   (`.handlers` and `.handle_error`), and the pre-existing filter only purged the first, so a
   "pinned" fetch was never actually blocking a redirect either. Both are now closed via one
   shared `pinned_http.strip_auto_redirect()` helper that purges both registries — a pre-existing
   vulnerability in already-shipped fetch infrastructure, found only because this feature's
   review needed the same guarantee and checked it empirically.

A third, separate finding from the same review round: `backend/mcp/client.py`'s *ordinary tool-
dispatch* transport (distinct from `oauth.py`'s discovery/token-exchange code, and not itself
governed by this ADR's trust-class rule) carried the newly-minted OAuth bearer token on the stock
`urllib` opener too — closed the same way, by routing it through `strip_auto_redirect()` and
refusing any redirect outright (a legitimate MCP server has no reason to 3xx a JSON-RPC call, so
"refuse" costs no real functionality). `backend/mcp/stdio_bridge.py` was deliberately left on the
stock opener — it only ever forwards to this app's *own* operator-configured base URL with an
operator-set token, never a third-party server's, so the "URL a hostile server chose" premise this
whole ADR turns on does not apply there.

Three independent review rounds (one initial + two re-verifications, each with real-socket
probes proving the finding and then proving the fix) are recorded in the branch history; the
mechanism above — same-host-and-port, manually-revalidated redirects, shared strip helper — is
the shipped state, not the round-1 draft this ADR originally described.

## Agent provenance

- **Decided by:** implementing Claude agent, `lane/mcp-oauth-p3-flow`.
- **Lane / branch:** `.claude/worktrees/mcp-oauth-p3`, branch `lane/mcp-oauth-p3-flow`.
- **Alternatives weighed:** unconditional pinning (breaks legitimate local MCP servers);
  unconditional trust (reopens the SSRF hole the MCP connection's own exemption depends on not
  applying here).
- **Driving artifacts:** `.claude/rfcs/2026-08-14-mcp-oauth.md` §9 item 2 (the proposed rule,
  named "to be reviewed"); `backend/core/net_guard.py`/`backend/core/pinned_http.py` (the existing
  primitives this composes rather than reinvents); `tests/test_mcp_oauth.py`'s trust-class test
  group (the empirical proof of all four branches).
- **Why this way (one line):** the MCP connection's own "unguarded is fine, nothing
  model-reachable picks this URL" reasoning is sound for a human-typed server address and
  unsound for anything that server's own documents point at next — so the guard has to key off
  which of those two categories a given URL falls into, not apply uniformly to either.
