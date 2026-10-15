---
title: Phone shortcuts are one signed file and a setup code
status: Accepted
date: 2026-10-12
deciders: Claude + owner
---

## Context

Settings → iPhone Shortcuts served a **configured** `.shortcut` per download: the instance's
address and API token baked into an unsigned binary plist. iOS 15+ refuses to import unsigned
shortcut files, so the flagship "scan and it installs ready to run" path could not work on any
current iPhone. The QR the server rendered was also never shown by the UI: the button opened the
download on the computer itself, and the desktop app baked in `localhost`. Android had no path at
all beyond a paragraph in a repo-only doc.

## Decision

- Every shortcut ships as **one generic file, signed once at release** (`make
  ios-shortcuts-sign`, `shortcuts sign --mode anyone`), committed under
  `backend/services/shortcuts/signed/` with a manifest pinning each file's unsigned source and
  its signed bytes. A stale or swapped file is treated as missing and never served. A CI test
  fails until the files are current.
- The instance-specific values reach the phone as a **setup code** (`<address>|<token>`) that the
  shortcut's single import question asks for and splits.
- Phone setup is an app page, `/phone-setup`, rendered before the sign-in gate with the app's
  theme and UI kit. It reads a ticket-gated, auth-exempt `GET /api/shortcuts/setup` for the code
  and the install links. A ticket lasts 10 minutes, binds to the first device that opens it, and
  dies with any token rotation or password change. The configured download route is removed.
- Settings → **Phone shortcuts** adapts to the surface: a QR on a computer, a direct link on an
  iPhone, and the capture endpoint + token for Android (Tasker/MacroDroid).

## Rejected

- **Signing per download on the server**: macOS-only, and `--mode anyone` would send a file
  containing the API token to Apple.
- **Two import questions (address, token)**: two separate pastes on a phone keyboard. One code is
  one paste.

## Consequences

Installing costs one paste instead of zero. In exchange it works on every host OS, survives token
rotation by editing one Text action, and keeps the token out of every file. Each template change
needs a re-sign on a Mac before release.
