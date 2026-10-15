---
title: The app hands out pre-configured shortcuts via a short-lived download ticket
status: Accepted
date: 2026-07-28
deciders: repo owner (direction), implementing Claude agent
---

# The app hands out pre-configured shortcuts via a short-lived download ticket

## Context

The tap-to-log shortcuts shipped as files committed to `docs/shortcuts/dist/` in a **private**
repo, documented on a docs page. The owner's observation: *"how do normal users download it then? I
thought we surface it somewhere already, like in settings."*

They were right, and the gap was total — nothing in the app or on the marketing site referenced
the shortcuts at all. Cloning a private repo was the only way to obtain one. The capability shipped
without any distribution.

Two things make in-app delivery strictly better than a file on a website, rather than merely more
convenient:

1. **The instance can answer the setup questions itself.** It knows its own address and API token.
   A placeholder file makes the user retype a URL and paste a token *on a phone keyboard*, which is
   the worst part of the whole feature.
2. **It knows the address that actually works.** A self-hosted instance is routinely reached over a
   tunnel. `localhost:8080` is almost never what a phone should be told.

## Decision

**Settings → Developer → iPhone Shortcuts**, beside the API token, because it hands out that same
credential in a different wrapper — a token rotation and the downloads it invalidates should be
read as one thing.

### The base URL comes from the request, not from config

`detect_base_url` builds it from `X-Forwarded-Proto`/`X-Forwarded-Host`, falling back to `Host`.
The browser loading Settings reached us on some URL; that same URL is what the phone should use.
This is what makes the tunnel case work with no configuration at all — the owner runs local behind
a tunnel, and a config field would have to be updated every time an ephemeral tunnel URL changed.

Those headers are caller-controllable. That is acceptable **here and only here** because the value
is echoed straight back to the signed-in operator, displayed in an editable field, and never feeds
an authorization decision. Compare `api/capture._client_key`, which deliberately ignores
`X-Forwarded-For` — there the value picks a rate-limit bucket, so trusting it would let a caller
rotate out of their own limit. The distinction is what the value is *used for*, not whether the
header is trustworthy.

### A ticket, not the API token, authenticates the download

The download must be authenticated (the file embeds the instance's token) but the device fetching
it is a **different device from the one signed in** — a phone scanning a QR has no session cookie.
A cookie-gated URL would simply 401.

The obvious fix is putting the API token in the URL. That works and is what most self-hosted
projects do; it also writes a long-lived credential into a QR image, the access log, and anything
else that records URLs. Instead the signed-in browser mints an opaque ticket via
`POST /api/shortcuts/prepare`, and the phone redeems it at `GET /api/shortcuts/download?t=…`.

- **`/download` is exempt from `require_auth`** — the ticket is its credential. `/prepare` stays
  gated, because it is the call that decides which URL gets baked in. A test pins that exactly one
  of the two is exempt.
- **In-memory, 10-minute TTL, bounded at 64 outstanding.** Tickets are worthless after the TTL, so
  surviving a restart buys nothing and losing them on restart is the safe direction.
- **Deliberately not single-use.** iOS may fetch a URL more than once (a HEAD, a redirect hop), and
  a ticket that burns on first touch fails in a way the user cannot diagnose. The short TTL is what
  bounds exposure, not the use count.
- An absent or expired ticket is a flat **404**, not a 403 — an unauthenticated caller learns
  nothing about whether a ticket existed.

### One generator, two callers

The shortcut definitions moved from `scripts/shortcuts/` to
`backend/services/shortcuts/catalog.py`, parameterized by a `values` dict. Absent values →
placeholders plus import questions (the committed copies). Supplied values → pre-filled and **the
questions are dropped entirely**, so a configured install is a single tap.

The build script now imports the same catalog, so the file a stranger downloads from the repo and
the file the app hands its own operator cannot drift apart.

### The QR is a data URI in the prepare response

Not a second endpoint, so the ticket-bearing URL never becomes its own request the browser records
and caches. Not raw SVG markup, so the frontend renders an `<img>` rather than
`dangerouslySetInnerHTML`.

## Consequences

### Positive
- Installing on a phone is: open Settings, scan, tap Add. No typing, no AirDrop, no GitHub login,
  no Files detour, no `.shortcut` transport problem at all.
- Tunnel users get a working shortcut with zero configuration.
- The long-lived API token never appears in a URL, a QR, or an access log.

### Negative
- **A downloaded file contains the API token.** That is the point, but it means the file must not
  be forwarded; the UI says so at the moment of download. Rotating the token invalidates every
  previously downloaded shortcut, which is correct but will surprise someone.
- Tickets are per-process. A future multi-worker deployment would need them shared or sticky.
- **Signing is still required** for a frictionless install. No delivery channel avoids
  "Allow Untrusted Shortcuts" — only `shortcuts sign` on a Mac does, and that stays a manual step
  the UI can only explain.
- `segno` is a new dependency (pure Python, no transitive deps) for QR rendering.

## Agent provenance

- **Decided by:** the repo owner (that Settings is the right home, and that the tunnel URL must be
  detected), implemented by the Claude agent on this lane.
- **Lane / branch:** `claude/settings-shortcut-download`.
- **Alternatives weighed:** publish the files on the marketing site (rejected as the primary path —
  it cannot pre-configure anything, and the app can); API token in the download URL (rejected —
  writes a long-lived credential into logs and a QR); single-use tickets (rejected — undiagnosable
  failure when iOS refetches); a configured "public URL" setting (rejected — an ephemeral tunnel
  URL would need re-entering every time).
- **Driving artifacts:** `backend/api/shortcuts.py`; `backend/services/shortcuts/`;
  `tests/test_shortcuts_api.py`; `docs/shortcuts/ios-tap-to-log.md`;
  `docs/adr/2026-07-28-capture-endpoint.md`.
- **Why this way (one line):** the instance already knows every answer the install asks for, so the
  only real problem was getting a file to a second device — and that is a ticket, not a login.
