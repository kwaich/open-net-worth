---
number: 2026-10-01
title: Desktop and browser share existing book authentication
status: Accepted
date: 2026-10-01
deciders: Codex orchestrator; repo owner requested simultaneous desktop and browser access using existing credentials
---

# Desktop and browser share existing book authentication

## Context

The desktop-only HTTP gate rejected ordinary browsers before existing book authentication
could run. The owner requested desktop and browser access to one running book, with the
existing password, session cookie, API token and localhost-trust configuration. Loading the
same UI in a browser must not grant native file or folder privileges.

## Decision

We will allow requests without the private desktop header through to normal book
authentication and public UI assets. A valid private header continues to authenticate native
requests automatically. A presented invalid, empty or duplicate header fails closed, including
on public routes. Keep the exact owned Host/Origin checks and loopback-only listener. The
`/api/desktop` routes independently require native authentication.

Use persisted AuthStore settings in both the API gate and auth-status response. Environment
flags seed missing settings only; changes in Settings → Security take effect immediately.
New desktop books use the ordinary trusted-localhost default. Existing stored settings remain
unchanged, including books requiring a password on localhost.

Expose a no-argument native browser action that opens only the current owned backend origin.
Never transfer the launch credential to URLs, browser cookies or renderer JavaScript. The
browser uses ordinary book authentication. It shares the backend, financial write chokes and
live-update stream, and stops working when that backend stops. Do not launch a second backend.

The desktop server publishes a wildcard change event after completed successful API write
responses (POST/PUT/PATCH/DELETE). Both clients then revalidate through the existing event bus.
Read requests, unsuccessful responses, interrupted responses and non-HTTP scopes do not
publish. Ordinary server boot is unchanged; existing tool-level change publication continues.

This updates the transport portion of
[Desktop owns its runtime and native capabilities](2026-10-01-desktop-owns-runtime-and-native-capabilities.md).
Its remaining runtime ownership, native IPC, artifact isolation and capability restrictions
continue to apply.

## Consequences

### Positive

- Desktop and browser share one book without a new credential configuration or login flow.
- Persisted security settings govern API access consistently with the login screen.
- Browser/API authentication does not grant native folder-management privileges.

### Negative

- Trusted localhost permits other local clients to use ordinary APIs, as in server mode.
- The address may change after restart or switching books; browser tabs must reopen it.
- Browser access depends on the desktop app remaining alive and is limited to this computer.

### Follow-ups

LAN/public access needs a separately designed explicit listener option; it is not enabled.
CI exercises shared-client behavior with the bundled runtime and packaged host transport.

## Alternatives considered

- Keep all requests private to Electron: rejects the requested browser workflow.
- Copy the desktop token into browser URLs: exposes the native launch credential.
- Add a one-time browser sign-in exchange: duplicates the existing book login system.

## Agent provenance

- **Decided by:** Codex orchestrator, independently reviewed by the desktop shell agent.
- **Lane / branch:** `codex/desktop-app`, existing isolated desktop worktree.
- **Alternatives weighed:** private-only HTTP, shared token URLs, new sign-in exchange, existing auth.
- **Driving artifacts:** owner's request for simultaneous desktop/browser access and existing auth configuration.
- **Why this way (one line):** reuse one book's credentials while preserving native host authority.
