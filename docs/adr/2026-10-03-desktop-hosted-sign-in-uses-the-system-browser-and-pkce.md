# Desktop signs in to a hosted instance through the system browser, with PKCE

Status: accepted. Scope: desktop host (`desktop/src/main/hosted*.ts`); the server contract is fixed.

A hosted instance sits behind a sign-in gateway: a visitor without a session is redirected to a
portal page, and the gateway sets its own host-only session cookie after a one-time code is
redeemed at `/_gateway/callback`. The desktop needs a one-click sign-in and a one-click move of a
local book onto that instance.

## Decision

- **Sign-in happens in the system browser, never in the app window.** The app opens
  `<portal>/connect?client=desktop&code_challenge=…&app_state=…`; the portal redirects to
  `onw://auth?slug&code&state`, and the app loads
  `https://<slug>.<instance domain>/_gateway/callback?code&code_verifier` in its own window. The
  verifier is a PKCE secret (S256) that stays in main-process memory.
- **A link is honoured once.** `app_state` is generated per attempt, kept ~10 minutes, and consumed
  on first use; an unknown, stale or replayed state is refused, and the slug is validated
  (`^[a-z0-9]{1,32}$`) before any URL is built. Code and verifier are never logged or echoed in
  errors (Electron words load failures with the URL, so `loadHosted` swallows them).
- **A hosted window is a server-mode window** (client preload only, no local authority) in a
  persistent partition, so the gateway's 12-hour cookie survives a relaunch. Its one extra rule:
  a navigation or redirect to the portal's `/connect` is cancelled and restarts the browser flow.
- **The import is driven from the hosted page's own preload.** The gateway lets a state-changing
  request through only when it is same-origin, so the main process asks the hosted frame's
  preload (an IPC message the page cannot send or see) to make `status`, `validate` and `import`
  calls. No new capability is exposed to the remote origin.
- **The move reuses the portability endpoints**: the local `GET /api/data/export` zip is held in
  memory (capped at the server's 100 MB), validated by the hosted dry-run, and applied with
  `replace=true` only if the hosted book is fresh or the user confirms the overwrite.
- The portal and instance domain default to the production values and are overridable
  (`ONW_HOSTED_PORTAL_URL`, `ONW_HOSTED_INSTANCE_DOMAIN`) so the flow runs against a local stack;
  plain HTTP is accepted for loopback only.

## Consequences

- The local book is untouched by a move and stays the fallback; hosted back to local is the same
  export/import in the other direction.
- Credentials never travel (the export omits them), so a short checklist names the AI provider
  and each enabled connector to reconnect, never their secrets.
- A sign-in cannot start from a link the app did not initiate, so a cold-started app answers a
  stray link with an explanation instead of acting on it.

## Alternatives considered

- Sign-in inside the app window: rejected, WorkOS and passkeys belong to the browser.
- Loopback HTTP redirect instead of a custom scheme: rejected, the scheme is already the portal's
  contract and needs no listening socket.
- Writing the export to a temporary file: rejected, it would leave a plaintext book on disk across
  a browser round trip.

## Agent provenance

- **Decided by:** Claude (desktop engineer lane).
- **Lane / branch:** `desktop-hosted-signin`, stacked on the gateway-mode branch.
- **Why this way:** reuse the server-mode window and the existing import endpoints; keep the remote
  origin's power exactly what server mode already allows.
