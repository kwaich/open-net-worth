---
title: A forgotten password resets only from the desktop app or the terminal
status: Accepted
date: 2026-10-04
deciders: Repo owner, Claude
---

# A forgotten password resets only from the desktop app or the terminal

## Context

Settings → Security and the lost-password page told people that opening the app on the server's
own machine (loopback is trusted by default) lets them set a new password. It never did:
`POST /api/auth/change-password` required the old password whenever one was set, loopback or not.
Someone who forgot the password had no supported way back in short of editing the database.

## Decision

1. A new password may be set WITHOUT the old one in exactly two ways:
   - **`onw reset-password`**, run on the machine that holds the book. It resolves the working
     folder exactly as `onw start` does, prompts twice with `getpass` (or `--password-stdin`),
     and writes through `AuthStore.replace_password` — the hash and a rotated session key in one
     serialized write — so it is safe beside a running server and signs every browser out.
   - **The desktop app's own session credential** (`request.state.desktop_authenticated`, set by
     `DesktopTransportMiddleware` only for a request carrying the per-launch random
     `X-ONW-Desktop-Session` header that Electron injects into trusted, owned-origin frames and
     strips from everything else, on a loopback-only socket with a pinned Host/Origin). A web
     page cannot read or forge it.
2. Everyone else keeps the rule: a set password needs the correct `old_password`. Being on
   loopback never waives it, with or without `trust_loopback`. There is no loopback-based
   bypass, because "the request came from this machine" is true of every local process and every
   browser tab, which is the very thing the old password is meant to tell apart.
3. One minimum length, `core.security.MIN_PASSWORD_LENGTH = 8`, enforced server-side in
   `change_password` and in the CLI, and mirrored by Settings and the first-run step.
4. `GET /api/auth/settings` reports `password_set` and `current_password_required` (the server's
   answer for that request) so the Password panel renders Set / Reset / Change without guessing
   from the client.

5. `POST /api/auth/remove-password` follows the same gate (correct current password, or the desktop
   credential; never loopback). It clears the hash and rotates the session key in one write, so the
   instance is "not configured" again: `require_auth` opens loopback and refuses the network. It is
   refused with 409 while `AUTH_PASSWORD` / `AUTH_PASSWORD_HASH` is set, because `AuthStore.get()`
   would re-seed the empty hash from them and the removal would silently not stick. It is also
   refused (409) outside the desktop app while `trust_loopback` is off, since removal would open
   loopback and override that choice.
6. Both writes share the login rate limiter for old-password guesses and validate (length, 409s)
   BEFORE checking the old password, so status codes are no oracle; both require
   `X-Requested-With` (or a Bearer token), like `rotate_api_token`. `onw reset-password` does not
   rotate the API token and says so.

## Consequences

- The Docker image has no `onw` entry point; the documented command there is
  `docker compose exec app python3 -m backend.cli reset-password`. `AUTH_PASSWORD` only seeds an
  empty store and is not a reset.
- A server with no shell access and no desktop book has no remote recovery. That is intended.
- No backward-compat shim: `rotate_session_key` was folded into `replace_password` and removed.
