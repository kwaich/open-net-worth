# Acceptance criteria — MCP OAuth sign-in (frontend)

Interaction-level GIVEN/WHEN/THEN for the OAuth sign-in surface added on top of
`connectors-redesign.md` — the Sign-in button, the popup + poll lifecycle, the RFC §4.6
degradation table, editing an OAuth server, and the Reconnect affordance for a dead sign-in.
Design: `.claude/rfcs/2026-08-14-mcp-oauth.md` (§4.2 the flow, §4.3 the callback route's four
hard questions, §4.6 the exact failure-message table, §5.3 the frontend file list). D13 in
`frontend/design_handoffs/connectors_handoff/PARITY_AND_GAPS.md` moves from divergence to
resolved once every criterion here passes.

**Out of scope for this file** — RFC §7 QA items 1–4 and 8 (adding Mercury / Avalara /
Expensify / Twelve Data end to end against a REAL vendor account, and reachability over a
tunnel hostname) require the owner's own credentials and are explicitly reserved for a human
with them. Everything below is the mockable subset (RFC §7 items 5, 6, 7, 9, 10 in spirit): the
UI's handling of every response shape the backend can return, which is fully testable without a
live vendor.

The QA agent must exercise every criterion against the RUNNING app and report PASS/FAIL per ID.
"It renders" is not a pass — every control gets clicked and its outcome asserted. Where a
criterion is already covered by an automated test, that test is named as its regression guard;
QA should still confirm the behavior live in the browser at least once.

---

## 1 · The Sign-in button replaces the old dead end

### MCPOAUTH-PICKER-OFFERS-SIGNIN
- GIVEN the add flow, step 1, Remote server transport
- WHEN I set Authentication to **OAuth**
- THEN I see a **Sign in** button and no other control — never the retired "OAuth sign-in
  isn't available yet" note.
- AND **Test connect** in the footer is disabled until sign-in completes.
- Regression guard: `frontend/src/components/tabs/connectors/catalog.test.tsx` ("lands an
  OAuth-only server on the real Sign-in flow, URL pre-filled").

### MCPOAUTH-PRESET-LANDS-ON-SIGNIN
- GIVEN the directory, MCP family
- WHEN I open **Mercury**, **Avalara AvaTax**, **Expensify**, or **Twelve Data**
- THEN step 1 opens with the URL already filled from the catalog preset and Authentication
  already on **OAuth** — I only have to click Sign in, never type the endpoint myself.

## 2 · The popup + poll lifecycle

### MCPOAUTH-POPUP-OPENS
- GIVEN step 1 with OAuth picked and a URL filled
- WHEN I click **Sign in**
- THEN a separate browser window/tab opens (never an iframe, never a full-page redirect away
  from this tab) and the add-flow modal shows **"Waiting for sign-in…"** with a spinner, still
  on step 1 — the modal never navigates away and never unmounts.
- Regression guard: `frontend/src/components/tabs/connectors/DirectoryModal.test.tsx` ("OAuth
  sign-in — the popup + poll lifecycle" — "opens a popup synchronously and shows a waiting
  state").

### MCPOAUTH-SIGNIN-COMPLETES
- GIVEN the waiting state is showing
- WHEN the sign-in completes (mock the poll answering `{status: 'ok'}`, or complete a real
  vendor consent screen)
- THEN the status area reads **Signed in**, **Test connect** becomes enabled, and the popup
  closes on its own — I do not have to close it by hand.
- AND clicking **Test connect** now reaches the server and lists its tools, exactly like any
  other successful Test connect (review step: Name, tools, permission ladder).
- Regression guard: `DirectoryModal.test.tsx` ("flips canConnect once the poll resolves ok").

### MCPOAUTH-CANCEL-CLEAN
- GIVEN the waiting state is showing
- WHEN I click **Cancel sign-in** (the inline control beside the spinner — distinct from the
  modal's own Cancel/Discard)
- THEN the popup closes, the status area returns to a plain **Sign in** button, and every other
  field on the form (URL, transport, whatever I had typed) is untouched — the add flow is still
  usable, not reset or closed.
- AND no error is shown — cancelling is not a failure.
- Regression guard: `DirectoryModal.test.tsx` ("Cancel sign-in stops the wait…").

### MCPOAUTH-CLOSE-POPUP-BY-HAND
- GIVEN the waiting state is showing
- WHEN I close the popup window myself, WITHOUT completing the vendor's consent screen
- THEN the add flow notices (via its poll) and lands on an honest inline message rather than
  waiting forever — the Sign-in button is available again to retry.

### MCPOAUTH-STATE-EXPIRES
- GIVEN a sign-in was started and abandoned
- WHEN more than 10 minutes pass (`oauth_pending.TTL_SECONDS`) and I then complete or poll that
  same flow
- THEN it reads as expired/unknown, not as a stack trace or a hang — retry with a fresh Sign in.

## 3 · Every RFC §4.6 failure reads its OWN message

### MCPOAUTH-REASON-NOT-OAUTH
- GIVEN `begin()` answers `{ok:false, reason:'not_oauth'}` (mock, or point Sign-in at a server
  that answers `200`/`400` unauthenticated)
- THEN the message reads *"This server didn't ask for a sign-in. Try None — it may not need
  credentials."* — verbatim, not a generic failure string.

### MCPOAUTH-REASON-NO-METADATA
- GIVEN `reason:'no_metadata'`
- THEN the message reads *"This server doesn't publish OAuth sign-in details, so we can't sign
  you in automatically. Use an API key if the vendor issues one."*

### MCPOAUTH-REASON-AS-METADATA-UNREACHABLE
- GIVEN `reason:'as_metadata_unreachable'`
- THEN the message names the host it found and says it couldn't read its configuration — never
  a raw exception.

### MCPOAUTH-REASON-NO-REGISTRATION
- GIVEN `reason:'no_registration', needs_client_id:true`
- THEN the message reads *"This server needs an app registration we can't create
  automatically."* AND a **Client ID** field appears (the manual escape hatch) — absent for
  every other reason.
- AND typing a client id and clicking Sign in again retries `begin()` WITH that `client_id`.
- Regression guard: `DirectoryModal.test.tsx` ("shows the manual Client ID field only for
  no_registration").

### MCPOAUTH-REASON-DCR-REJECTED
- GIVEN `reason:'dcr_rejected'` with `redirect_uris`
- THEN the vendor's own error text is shown, PLUS the exact redirect URI(s) this instance asked
  to register, in a copyable monospace block — so the user can register it by hand. No Client
  ID field on this reason.
- Regression guard: `DirectoryModal.test.tsx` ("shows the redirect URIs to register by hand
  only for dcr_rejected").

### MCPOAUTH-REASON-INSECURE-REDIRECT
- GIVEN `reason:'insecure_redirect'` (this instance reached over plain HTTP on a non-loopback
  host)
- THEN the message reads *"Sign-in needs a secure address. Reach this instance over HTTPS, or
  from the machine it runs on."*

### MCPOAUTH-REASON-BLOCKED-URL
- GIVEN `reason:'blocked_url'` (an SSRF-guard refusal on a discovery URL)
- THEN a specific, non-generic message is shown — never a stack trace, never "undefined".

### MCPOAUTH-NO-GENERIC-FALLBACK
- GIVEN at least 3 of the reasons above, tested back to back
- WHEN I compare their messages
- THEN each is textually DIFFERENT and names what actually happened — proving the UI is keyed
  by `reason`, not collapsed to one fallback string.
- Regression guard: `DirectoryModal.test.tsx` ("renders the RFC §4.6 message for each distinct
  begin() failure reason, not one generic fallback").

## 4 · Editing an OAuth server

### MCPOAUTH-EDIT-ROUNDTRIPS
- GIVEN a saved server with `auth_type: 'oauth'`
- WHEN I open kebab → **Edit connection**
- THEN Authentication reads **OAuth** — never silently shown as API key.
- Regression guard: `frontend/src/components/tabs/connectors/McpEditFlow.test.tsx` ("editing
  an OAuth server" — "round-trips oauth as the picker value").

### MCPOAUTH-EDIT-NO-TOKEN-FIELD
- GIVEN the same edit form
- WHEN I look at the OAuth section
- THEN there is **no raw token/API-key input anywhere on the screen** — instead a
  connected-account summary (issuer host + scope) and a **Sign in again** control.
- Regression guard: `McpEditFlow.test.tsx` ("shows the connected-account summary, never a raw
  token field").

### MCPOAUTH-EDIT-VALID-SKIPS-REAUTH
- GIVEN a saved server whose sign-in is still valid (`connected: true`, `needs_reauth: false`)
- WHEN the edit form opens
- THEN **Test connect** is already enabled — I am not forced through a fresh popup just to
  re-verify a working connection.

### MCPOAUTH-EDIT-DEAD-SIGNIN
- GIVEN a saved server whose sign-in has gone dead (`needs_reauth: true`)
- WHEN the edit form opens
- THEN it shows the plain **Sign in** button (not "Signed in"), a note that the connection WAS
  connected to `<issuer host>` until it expired, and Test connect is disabled until a fresh
  sign-in lands.
- Regression guard: `McpEditFlow.test.tsx` ("a dead sign-in (needs_reauth) shows the plain
  Sign-in button").

## 5 · Reconnect, for a dead sign-in

### MCPOAUTH-RECONNECT-VISIBLE
- GIVEN an OAuth server whose status reads **Auth error** (`last_status: 'auth_error'`)
- WHEN I view its list row and its detail page
- THEN both show a **Reconnect** action — the row's small one and the detail page's primary
  button.

### MCPOAUTH-RECONNECT-IS-A-SIGNIN
- GIVEN that Reconnect action
- WHEN I click it
- THEN it opens the SAME popup + poll sign-in as the add flow (`begin({server_id})`, reusing
  the existing DCR registration — only consent is re-obtained) — NOT a bare re-test, which
  would just fail again against the same dead token.
- AND on success, the row/detail page refresh to a healthy status and a confirmation toast.
- Regression guard: `McpEditFlow.test.tsx` ("Reconnect" — "an OAuth server reconnects with a
  fresh popup + poll sign-in, not a bare retest").

### MCPOAUTH-RECONNECT-NON-OAUTH-UNCHANGED
- GIVEN a NON-OAuth MCP server in a `connect_error`/`error` state
- WHEN I click Reconnect
- THEN it still re-runs the plain `POST /api/mcp-clients/{id}/test` — this criterion guards
  against the OAuth branch accidentally swallowing every other server's Reconnect too.
- Regression guard: `McpEditFlow.test.tsx` ("a plain MCP server still reconnects by
  re-testing — unchanged").

### MCPOAUTH-RECONNECT-BUSY-STATE
- GIVEN I have just clicked Reconnect on an OAuth server's detail page
- WHEN the sign-in is in flight
- THEN the button reads **Reconnecting…** and is disabled — a second click does not open a
  second popup.
- Regression guard: `frontend/src/components/tabs/connectors/ConnectorDetail.test.tsx` ("says
  Reconnecting… and disables the button…"); `McpEditFlow.test.tsx` ("a second click while a
  popup is already open does not fire a duplicate sign-in").

## 6 · Empty / loading / error states (UI Quality Gate)

### MCPOAUTH-EMPTY-NO-SUMMARY
- GIVEN a server that has never signed in (`oauth: null` / `connected: false`, no prior
  sign-in)
- WHEN I view its edit form or detail page
- THEN no connected-account summary is fabricated — just the plain Sign-in affordance.

### MCPOAUTH-LOADING-WAITING-STATE
- GIVEN a sign-in in flight
- WHEN I look at the status area
- THEN a spinner + "Waiting for sign-in…" is visible the whole time the popup is open — never a
  bare disabled button with no explanation.

### MCPOAUTH-ERROR-NEVER-BLANK
- GIVEN any of the failure reasons in §3, or a network failure while calling `begin()`/the poll
- WHEN the failure lands
- THEN there is always a human-readable message and the Sign-in button remains available to
  retry — never a blank status area, never an uncaught exception in the console.

---

## Legacy modal note

`frontend/src/components/tabs/connectors/mcp.tsx`'s old outbound-server add/edit form
(`McpServerFormModal`/`McpServerBody`) was DEAD CODE before this change (nothing imported it —
superseded by `DirectoryModal` + `McpAddFlow` + `McpEditModal` + `ConnectorDetail`) and has been
deleted rather than taught OAuth. `McpAccess` (the INBOUND "connect an external client to this
instance" panel that file also hosts) is unrelated and untouched — confirm Settings → Developer
still renders it normally.
