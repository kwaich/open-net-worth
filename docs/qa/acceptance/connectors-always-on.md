# Acceptance criteria — always-on status connectors (Yahoo Finance, CoinGecko)

Interaction-level GIVEN/WHEN/THEN for always-on connectors — status-only data sources that
provide pricing without requiring user-initiated connections. Yahoo Finance and CoinGecko are
migrated from Settings panels into real Connector cards, but as read-only status-display
connectors with no Pause/Connect/Resume primary actions. The QA agent must exercise every
criterion and report PASS/FAIL per ID.

Companion to `connectors.md` and `connector-secret-reveal.md`, which cover the general
connectors flow. This file covers the DISTINCT interactions of always-on status connectors:
their detail-modal action states and their configuration controls.

---

## 1 · Always-on detail modal — no primary action

### AO-DETAIL-NO-PRIMARY-ACTION
- GIVEN an always-on connector (e.g. Yahoo Finance, CoinGecko) is displayed in its detail modal
- WHEN I look at the detail page's primary action area (top right, where Pause/Connect/Resume
  normally appear)
- THEN there is NO primary action button (Pause, Connect, Resume, or Reconnect) — only the
  kebab menu (Edit, Remove, etc.) remains.
- AND the status badge still communicates the connector's health (e.g. "Active", "Error").

### AO-DETAIL-KEBAB-INTACT
- GIVEN an always-on connector's detail modal is open
- WHEN I click the kebab menu icon
- THEN it shows the standard items (View details, Edit configuration, Refresh tools list,
  Remove connector) — NOT a dimmed or disabled menu, and the actions themselves work.

### AO-DETAIL-DESCRIPTION
- GIVEN an always-on connector detail page is visible
- WHEN I read the page
- THEN the connector's description is shown (if present) — the same layout as any other
  connector, just without the primary action button.

---

## 2 · Yahoo Finance — the "Use Yahoo Finance first" toggle saves on change

### YF-TOGGLE-RENDER
- GIVEN I open the Yahoo Finance connector detail page and access its configuration
- WHEN the config form loads
- THEN a toggle labeled "Use Yahoo Finance first" (or similar) is visible in the form.

### YF-TOGGLE-LIVE-SAVE
- GIVEN the "Use Yahoo Finance first" toggle is visible and currently OFF
- WHEN I click it to turn it ON
- THEN the toggle visually changes state immediately (no separate Save button interaction
  required) AND a write request to the API is fired (inspect the network tab).

### YF-TOGGLE-PERSISTS
- GIVEN I have toggled "Use Yahoo Finance first" ON
- WHEN I leave the configuration (back-arrow or close) and return to the same connector
- THEN the toggle shows ON — the setting was persisted and is not lost on navigation.

### YF-TOGGLE-ERROR-FEEDBACK
- GIVEN the toggle is ON and I toggle it OFF while the backend is unreachable (or returns an error)
- WHEN the toggle fires its save request
- THEN an inline error message appears near the toggle, and the toggle reverts to its previous
  state (ON) — the UI does not claim success when the write failed.

---

## 3 · CoinGecko — the reveal-eye shows the stored Demo API key

### CG-EYE-FIELD-RENDER
- GIVEN I open the CoinGecko connector detail page and access its configuration
- WHEN the config form loads
- THEN a credential field (labeled "Demo API Key" or similar) is visible with a masked value
  (shown as `••••••••`) and an eye icon next to it.

### CG-EYE-REVEALS-SAVED-VALUE
- GIVEN the CoinGecko Demo API key field shows a masked value and the field is empty
- WHEN I click the eye icon
- THEN the saved value appears IN the same input field, unmasked, and the eye icon changes
  to indicate the value is now visible (no separate reveal dialog or second input appears).

### CG-EYE-HIDES-AGAIN
- GIVEN the Demo API key field is currently revealing a saved value
- WHEN I click the eye icon again
- THEN the field returns to empty and masked state — the "keep the saved value" state
  returns.

### CG-EYE-EDIT-SURVIVES-HIDE
- GIVEN I reveal the saved Demo API key and then type a different value over it
- WHEN I click the eye to hide it
- THEN the field re-masks BUT keeps my typed value, and the Save button becomes enabled
  (indicating a pending change).

### CG-SAVE-ON-EDIT
- GIVEN I have edited the Demo API key field (either by revealing and changing, or pasting a
  new value)
- WHEN I click the Save button in the modal footer
- THEN the write lands, and the field returns to masked state showing the new saved value.

### CG-NO-FALSE-DIRTY-ON-REVEAL
- GIVEN the config form has no edits
- WHEN I reveal the saved Demo API key and do nothing else
- THEN the Save button remains DISABLED — merely looking at the key does not mark the form as
  dirty.

---

## 4 · Common form behaviors — Edit configuration modal

### AO-CONFIG-BACK-ARROW
- GIVEN any always-on connector has its configuration form open
- WHEN I click the `‹ <name>` back-arrow at the top of the modal
- THEN I return to the connector's detail page without saving any unsaved edits.

### AO-CONFIG-CLOSE-X
- GIVEN any always-on connector has its configuration form open
- WHEN I click the X button or press Escape
- THEN the modal closes and I return to the detail page (same behavior as the back-arrow).

### AO-CONFIG-SAVE-DISABLES-ON-NO-CHANGES
- GIVEN a freshly opened configuration form with no edits
- WHEN I look at the Save button in the footer
- THEN it is disabled until I make a change to any field.

