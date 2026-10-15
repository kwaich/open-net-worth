# Acceptance criteria — revealing a saved connector secret, and one Save

Interaction-level GIVEN/WHEN/THEN for two "one control, not two" changes to the Connectors
detail page's config view. The QA agent must exercise every criterion against the RUNNING app
and report PASS/FAIL per ID. "It renders" is not a pass — every control gets clicked and its
outcome asserted.

Companion to `connectors-redesign.md`, which covers the list/detail/permissions/directory
surfaces this one does not touch. Design decision + its security boundary:
`docs/adr/2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not.md`.

**Setup.** Use a QA instance (`ONW_QA_MODE=1`, isolated dirs, port 8090–8099) with at least
Telegram, one web-search provider, IBKR and one stdio MCP server configured with real-looking
placeholder credentials. Never drive `:5174`.

---

## 1 · The eye reveals the SAVED value

### SEC-EYE-REVEALS
- GIVEN a connector with a saved credential (its field shows `••••••••` and the box is empty)
- WHEN I click the eye on that field
- THEN the saved value appears IN the same input, unmasked and editable in place
- AND no second input, echo row, dialog or confirmation step appears.

### SEC-EYE-ONE-CONTROL
- GIVEN any connector credential field
- WHEN I look at it
- THEN there is NO "Reveal saved password / token / key" button anywhere beside it, and no
  read-only echo row with its own Copy/Hide pair.
  *(Regression guard: three connectors shipped exactly that second control.)*

### SEC-EYE-EVERY-FAMILY
- GIVEN each of: Telegram (bot token), Email → Receive (app password), Email → Send (SMTP
  password), Webhook (secret), Plaid (secret), Enable Banking (private key), Yodlee (client
  secret AND user password), Alpaca (API key id AND secret key), IBKR (Flex token), Web search
  (API key), an MCP server (API key)
- WHEN each has a saved value and I click its eye
- THEN the saved value is shown. A field where the eye does nothing is a FAIL.

### SEC-EYE-HIDE
- GIVEN a revealed value I have NOT edited
- WHEN I click the eye again
- THEN the box returns to empty and masked — the "keep the saved value" state.

### SEC-EYE-NOTHING-SAVED
- GIVEN a connector with NO saved credential
- WHEN I look at its credential field
- THEN it behaves as an ordinary password box: the eye unmasks what I type, and clicking it on
  an empty box does not call the server.

### SEC-EYE-ENV-ONLY
- GIVEN a web-search provider whose key comes only from an environment variable (the card says
  a key is set) and whose `search_config` row already exists
- WHEN I click its eye
- THEN the field shows "Could not read the saved value" rather than silently doing nothing or
  showing an empty box.
  *(Deliberate: reveal reads the store, never `os.environ` — see the ADR.)*

---

## 2 · Looking at a secret must not change it

### SEC-NO-REWRITE
- GIVEN I reveal a saved credential and change nothing else
- WHEN I leave the form via the `‹ <connector>` back control
- THEN no write request is made and the credential is unchanged.

### SEC-NO-FALSE-DIRTY
- GIVEN a form with no edits
- WHEN I reveal a credential and do nothing else
- THEN the footer's **Save** stays DISABLED — looking at a key is not editing it.
- AND when I then edit the revealed value, Save becomes enabled.

### SEC-EDIT-SURVIVES-HIDE
- GIVEN I reveal a credential and then type a different value
- WHEN I click the eye to hide it
- THEN the box re-masks but KEEPS my typed value, and saving replaces the credential.

### SEC-MCP-ENV-NO-WIPE
- GIVEN an MCP stdio server with saved environment variables
- WHEN I open Edit connection, leave the env box untouched, and complete the save
- THEN every saved env value survives (verify by revealing them again afterwards).
  *(`env` is a whole-map REPLACE with no keep-rule — this is the dangerous direction.)*
- AND when I instead click the env box's eye, the saved `KEY=value` lines load into the box;
  hiding them again empties it, and completing the save still wipes nothing.

---

## 3 · One primary action, in the footer

### SEC-ONE-SAVE
- GIVEN I open any connector's config view
- WHEN I read the screen
- THEN there is exactly ONE primary action — **Save**, in the modal footer — and no `Save` /
  `Save credentials` / `Save settings` button in the form body, and no `Done`.
  *(Regression guard: the view shipped with a body Save AND a footer Done, where Done
  silently discarded edits.)*

### SEC-SAVE-DIRTY-GATE
- GIVEN a freshly opened config view with no edits
- WHEN I look at the footer
- THEN **Save** is disabled; editing any field enables it.

### SEC-SAVE-CLOSES-ON-SUCCESS
- GIVEN a valid edit
- WHEN I click **Save**
- THEN the write lands, the view returns to the connector's detail page, and the detail page
  reflects the change (e.g. status moves to Connected / a `has_*` badge appears).

### SEC-SAVE-KEEPS-OPEN-ON-FAILURE
- GIVEN a save that the server rejects (stop the backend, or submit an invalid value)
- WHEN I click **Save**
- THEN the view STAYS OPEN with the form's error message visible, and does NOT close over a
  change the server never took.

### SEC-SAVE-EMAIL-ONE-SAVE-TWO-WRITES
- GIVEN the Email connector's unified Account/Receive/Send screen (2026-08-17 — IMAP and SMTP
  are no longer two tabs; a change to either leg is edited on the same screen)
- WHEN I edit a field and click the footer **Save**
- THEN BOTH `PUT /api/email/config` (IMAP) and `PUT /api/channels/email` (SMTP) fire from the
  one action, and the screen reports a combined error naming whichever leg failed if either
  write is rejected.

### SEC-SAVE-OTHER-ACTIONS-INTACT
- GIVEN the forms that carry non-save actions
- WHEN I open each
- THEN these still work: Plaid **Link bank** / **Complete link** / **Sync now**, Enable Banking
  **Link bank** / **List banks** / the country + institution pickers, Yodlee **Sync now**,
  Alpaca **Sync now**, IBKR **Sync now**, Web search **Use for Lucius**, Email/Webhook **Send
  test**.

### SEC-BACK-DISCARDS
- GIVEN unsaved edits in a config view
- WHEN I click the `‹ <connector>` back control
- THEN I return to the detail page and nothing was written.

---

## 4 · The model still cannot see any of it

Backend-verifiable; `tests/test_connector_secrets_model_isolation.py` is the automated form.
Re-check by hand if the tool surface changes.

### SEC-MODEL-BLIND
- GIVEN every connector holds a saved credential
- WHEN I ask Lucius to list the MCP servers, read a connector's permissions, enable a
  connector, or report a connector's status
- THEN no credential value appears in any tool result — only ids, names, redacted endpoint
  hints, and `has_*`-style flags.

### SEC-LIST-PAYLOAD-CLEAN
- GIVEN the same
- WHEN I inspect `GET /api/connectors` and `GET /api/connectors/{id}` in the network panel
- THEN neither response contains a credential value. Only
  `GET /api/connectors/{id}/secret/{field}` ever does, and only after I click an eye.

### SEC-AUTH-REQUIRED
- GIVEN a configured instance with `AUTH_TRUST_LOOPBACK=false`
- WHEN an unauthenticated client requests `GET /api/connectors/{id}/secret/{field}`
- THEN it is 401 and the response body contains no credential.

### SEC-NO-STORE
- GIVEN a successful reveal
- WHEN I inspect the response headers
- THEN `Cache-Control: no-store` is present.

### SEC-BACKUP-STILL-STRIPPED
- GIVEN saved credentials
- WHEN I download a sanitised backup
- THEN `secret_config` and `mcp_clients` rows are still stripped from it — reveal changed a
  read path, not how secrets are stored or exported.
