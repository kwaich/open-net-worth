---
title: The public demo needs no password — and only a demo can be opened that way
status: Accepted
date: 2026-10-07
deciders: Repo owner, Claude
---

# The public demo needs no password — and only a demo can be opened that way

## Context

`demo.opennetworth.ai` published its password (`demo@opennetworth.ai`) so anyone could try the
app. That is friction for a visitor and no protection for the host: the password was public, and
ADR `2026-10-05-a-public-demo-visitor-is-an-operator-who-cannot-reach-past-the-book` already treats
every visitor as the operator and refuses, in `api/demo_lockdown.py` and the store guards,
everything that reaches past the fabricated book. The login screen was a speed bump with no
security value.

Removing it must not weaken anything else. Open Net Worth's auth is a fail-closed gate
(`api/_deps.require_auth`): an unconfigured or misconfigured remote is refused, `auth_disabled`
and loopback trust are stored settings, and a desktop or Docker user must never become
passwordless because of an env typo, a stored setting, a header, or a missing variable.

Forces:

- The decision must be **process-level**, resolved from the environment at boot, so nothing a
  request carries can reach it.
- The per-visitor Lucius quotas (`demo_quota`) are keyed by `X-Forwarded-For` / peer address, never
  by a login or cookie, so they must keep working with no session.
- A passwordless visitor must still be unable to set, change or remove the password, or change any
  auth setting — that would lock every other visitor (and the host) out.

## Decision

We will make the demo passwordless with a **second, separate opt-in** that is meaningless without
`DEMO_MODE`:

1. `Settings.demo_open_access` (`DEMO_OPEN_ACCESS`, env only) is read **only** through
   `core.config.demo_open_access_active()`, which is `demo_mode and demo_open_access`. Either flag
   alone, a falsy value, or a typo (`_env_bool` fails to false) leaves the instance gated. There is
   no stored setting and no header for it.
2. `require_auth` returns early when `demo_open_access_active()`, and `GET /api/auth/status`
   answers `{"required": false, "authenticated": true, "mode": "demo"}`. No session is issued: a
   visitor holds no cookie, and the Bearer-only `/mcp` endpoint keeps its own gate untouched.
3. The demo book still carries a **password nobody knows** (a random one per launch in
   `scripts/run_demo.sh`, minted once on the VM by `demo-aws.sh`, never printed or published). The
   instance therefore stays "configured": first-run set-a-password is closed, loopback is not
   trusted, and the instance API token is kept. The launcher does not use it.
4. The lockdown grows to cover the remaining ways a passwordless visitor could change auth state:
   `PUT /api/auth/settings` (it holds `auth_disabled`, `trust_loopback` and the session TTL) and
   the iOS Shortcut routes (`/api/shortcuts/prepare|download`, whose file embeds the API token),
   next to the password and API-token routes it already refused. A demo is the only place these
   refusals apply, so a normal instance is unchanged.
5. The SPA treats `mode: "demo"` like `gateway`: no Login screen, no logout item (it keys on
   `required`). The optimistic-auth cache key is bumped to `onw-auth-status-v2`, so a visitor who
   bounced off the old password screen and has `authenticated: false` cached is never shown Login
   on return.
6. `desktop.configure_environment` strips `DEMO_OPEN_ACCESS` along with `DEMO_MODE`, so a desktop
   app launched from a shell that exports them cannot open itself.

## Consequences

### Positive

- A visitor lands straight in the app; the demo's security posture is the lockdown, which is now
  exercised on a credential-less visitor by `tests/test_demo_open_access.py`.
- The failure mode of a bad flag is "still asks for a password", never "open". A real instance
  would need BOTH flags set deliberately.
- Per-visitor quotas, rate-limit keys and the `demo-aws.sh lucius` flow (a separate
  `DEMO_MODE=0` process, for which `DEMO_OPEN_ACCESS` alone is inert) are unaffected.

### Negative

- Cross-site request protections that `require_auth` applies to cookie sessions (same-origin
  writes, host allowlist) do not run for an open demo. There is no session or credential for a
  cross-site page to ride, and anyone can already call the API directly, so this exposes nothing
  a visitor could not do; it would be wrong to copy for any instance holding real data.
- Bumping the cache key costs every existing browser one blocking splash on its next visit.
- A demo host must not set `DEMO_MODE` + `DEMO_OPEN_ACCESS` on a real book. `.env.example` says so;
  the guard is deployment discipline, as it already was for `DEMO_MODE`.

### Follow-ups

- A new route that changes auth state or hands out the API token joins `demo_lockdown._LOCKED`
  (`tests/test_demo_lockdown.py` and `tests/test_demo_open_access.py` pin today's set).

## Alternatives considered

- **Issue a normal session cookie to every demo visitor** — rejected: it keeps a login-shaped
  code path on the demo, mints a real session (signed with the demo's key, outliving a reseed)
  for anonymous traffic, and still needs a "log in automatically" endpoint that is an
  unauthenticated session-minting route; a bug in its guard would be a real-instance bypass.
- **Treat `DEMO_MODE` alone as authenticated** — rejected: `.env.example` documented it as
  "seed the example data", `qa/boot.mjs` and the screenshot scripts set it on scratch books, and a
  single env var that opens an instance is the typo this ADR exists to prevent. The second flag
  makes opening deliberate.
- **`AUTH_DISABLED=1` for the demo** — rejected: it is a stored dev escape hatch, writable through
  `PUT /api/auth/settings`, honoured on loopback and in tests, and it leaves the instance
  unconfigured (no password, no kept token) and is one stored flag away from opening a real book.
- **A reverse-proxy rule that injects credentials** — rejected: the repo's gateway mode already
  models a trusted proxy, but it adds a required component to a single-container demo and moves
  the fail-closed guarantee out of the repo.

## Agent provenance

- **Decided by:** senior-engineer lane (Claude), finishing a half-done task.
- **Lane / branch:** `feat/demo-passwordless`, worktree `agent-aa7a86f6f04db98bc`.
- **Alternatives weighed:** issued session, `DEMO_MODE` alone, `AUTH_DISABLED`, proxy-injected credentials.
- **Driving artifacts:** ADR `2026-10-05-a-public-demo-visitor-is-an-operator-who-cannot-reach-past-the-book`,
  `docs/DEMO.md`, `tests/test_demo_open_access.py`.
- **Why this way (one line):** a process-level, env-only, two-flag opt-in that only a demo can
  satisfy keeps "can this instance become passwordless?" answerable by reading one function.
