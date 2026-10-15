---
number: 2026-10-01
title: Desktop owns its runtime and native capabilities
status: Accepted
date: 2026-10-01
deciders: Codex orchestrator and specialist implementers; repo owner requested separate-branch end-to-end implementation
---

# Desktop owns its runtime and native capabilities

## Context

The owner authorized implementation of the Electron desktop proposal. The existing Python
wheel already carries React and documentation, but a browser or separately installed server
cannot own native dialogs, application lifecycle, folder access or distribution. Financial
services and write chokes must remain authoritative. A localhost listener alone does not
prove that a request came from the desktop app.

## Decision

Ship macOS first with an Electron host and regular bundled CPython. Build the existing wheel
into a relocatable interpreter using a hashed dependency lock. Users need no Python, Node,
uv or Docker. The host defaults to `~/.open-net-worth`, matching the server application, unless
the user selected another book. An existing default book is opened; a missing one is created.
An active instance's book lock refuses desktop startup before migration. Desktop preferences
remain in OS application-data storage; earlier preview books are preserved for explicit folder
selection, and old folder grants remain scoped to their original book. The host chooses one
ephemeral IPv4 loopback port. A typed startup failure identifies a busy book and validates the
recorded owner's port; recovery shows the folder, port and retry/folder actions. Only a failure
matching the launched backend's protocol, instance id and version is accepted. It launches one
backend with a random credential through stdin, checks the
backend's protocol/instance/version identity, and owns shutdown. The credential never reaches
renderer JavaScript, command arguments, URLs or application logs.

Desktop transport requires the credential even before first-run setup. Chromium requests
receive it only for the owned application frame and the narrowly scoped documentation frame;
untrusted frames and external pages cannot use the backend or native IPC. Existing OAuth
callbacks keep their state validation and open in the system browser. Artifacts execute on
an isolated custom scheme, use the shared document-preparation helper, and have no native
bridge, application origin or network access.

Expose typed choices and preferences through a sandboxed preload. Keep folder locators and
OS preferences in host storage, but store grant revocation and ingest jobs in SQLite. Folder
intake creates an attachment and review job, never financial rows. Native files enter the
existing statement preview/decision/commit pipeline. Attachment-backed previews preserve
original identity and enforce the current Private flag before any model path. The actual
committed source determines which review job is completed.

Book selection is a no-argument native folder choice. Accept a complete working folder or an
empty folder, confirm the reload, and stop the current backend before starting the candidate.
Clear queued native commands and renderer storage, drain folder/notification polls, and keep
the same supervisor object so window policy observes the new backend identity. Remember the
candidate only after readiness and application load; otherwise restart the previous book.
Scope host folder locators and notification cursors by canonical book root. The desktop never
changes the browser/server's global root pointer. An explicit QA root remains a hard override.

Do not enable local sandbox, stdio MCP or external Builder execution in desktop v1. These
launch detached process groups which the host cannot reliably contain after a backend crash.
HTTP MCP and inherited model-provider CLI processes remain available. Re-enable detached
capabilities only with OS-owned process containment and crash tests. Keep browser/server
capabilities unchanged.

Updates in v1 are manual: a fixed official release link, portable backup, explicit Quit,
replace application, reopen. Migration ownership and ahead-of-code refusal remain in force;
never automatically restore an older data snapshot. Public macOS builds must be signed and
notarized. Local unsigned artifacts are development previews.

## Consequences

### Positive

- One installer includes the application, backend, fonts and documentation.
- Native file selection, exports, menus, tray activity, login preference and notifications
  reuse existing financial behavior.
- Folder permission revocation survives portable restore; imports remain reviewable after
  access is removed.
- The desktop backend fails closed against other local clients and hostile web origins.

### Negative

- Electron and CPython make a large download and add two runtime update responsibilities.
- Desktop v1 lacks detached subprocess capabilities and automatic updates.
- Login items, Gatekeeper and notification delivery require signed owner-device acceptance.
- Windows packaging configuration is scaffolding, not a verified Windows release.

### Follow-ups

Configure the `desktop-release` environment's Apple signing credentials; verify notarization,
login and notifications on a clean machine. Add Windows Job Objects/macOS process ownership
before detached capabilities. A signed update feed needs its own quiesce/backup/relaunch test.
Provider-specific browser automation remains a separate approval-and-verification workflow.

## Alternatives considered

- Tauri plus PyInstaller: smaller shell, but introduces a Rust/native integration layer and
  frozen-interpreter compatibility work beyond the existing Python ecosystem.
- Remote browser wrapper: leaves runtime, offline data, lifecycle and native permissions
  outside the application.
- Path checks around folder reads: directory replacement can race them; reads must be
  anchored to the granted directory descriptor.
- Enumerating detached descendants after a crash: races process creation and does not provide
  the ownership guarantee required to enable arbitrary local subprocesses.

## Agent provenance

- **Decided by:** Codex orchestrator, desktop backend and shell implementers, UI design review.
- **Lane / branch:** `codex/desktop-app`, isolated managed worktree.
- **Alternatives weighed:** Electron/regular Python, Tauri/frozen Python, remote shell.
- **Driving artifacts:** Desktop RFC,
  [implementation plan](../superpowers/plans/2026-10-01-desktop-app.md).
- **Why this way:** Preserve proven financial code while making native authority and runtime
  ownership explicit and testable.
