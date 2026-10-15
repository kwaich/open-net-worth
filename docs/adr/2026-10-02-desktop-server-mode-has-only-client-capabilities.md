---
title: Desktop server mode has only client capabilities
status: Accepted
date: 2026-10-02
deciders: Codex orchestrator and repo owner
---

# Desktop server mode has only client capabilities

## Context

Desktop local mode supervises Python, owns a book lock and grants native file/watch/export
capabilities to its authenticated local UI. The owner requested access to existing hosted or
localhost Open Net Worth servers, with initial choice and later Settings switching.
Granting the local preload to a hosted page would extend local machine authority to remote
code. Duplicating the server's login would also introduce unnecessary credential handling.

## Decision

We will use the selected server's UI and existing authentication in server mode, with a
separate restricted client preload and a target-isolated session. Only app-owned connection
pages can submit a new target. A remote page receives connection metadata, an action to open
that owned chooser, Open in browser and safe UI commands. It receives no full desktop bridge,
local book path, file grants, artifact protocol or desktop launch credential.

Local mode remains responsible for its backend and book. Server mode starts neither and
never stops the selected server. The last local book and successfully selected server are
remembered separately. Native Connection menu remains available independent of server UI.

## Consequences

### Positive

- Existing hosted and localhost servers reuse their current login and relative API requests.
- One server owns the book, whether viewed through browser or desktop.
- Local filesystem/provider authority does not cross into a hosted renderer.

### Negative

- In-app Settings features depend on the selected server's frontend version; older servers
  use the always-available native Connection menu.
- In-memory connection sessions may require login after restarting the desktop app.
- Server mode cannot use a laptop's CLI providers without a separate forwarding design.

### Follow-ups

Verify URL/TLS handling, private HTTP confirmation, authentication, mode-bound IPC, header
isolation, cancellation, rollback and actual native/browser switching in the acceptance suite.
Persistent per-server sessions and provider forwarding require their own scoped decisions.

## Alternatives considered

- Remote UI with the local preload: rejected because it grants local authority to remote code.
- Bundled UI plus API proxy: deferred because cookie/origin translation and version coupling
  add complexity beyond access to an existing server.
- External browser only: useful today but lacks a remembered desktop connection and switcher.

## Agent provenance

- **Decided by:** Codex orchestrator, with native engineer and UI designer.
- **Lane / branch:** `codex/desktop-app` in the managed desktop worktree.
- **Alternatives weighed:** server-served UI/restricted preload, full preload, bundled UI/proxy.
- **Driving artifacts:** [PRD](../prd/desktop-server-client.md),
  [RFC](../../.Codex/rfcs/desktop-server-client.md), owner's implementation request.
- **Why this way:** Reuse the server's authentication while keeping native authority local.
