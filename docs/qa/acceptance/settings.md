# Acceptance criteria — Settings tab

Interaction-level GIVEN/WHEN/THEN for the **Settings** tab. The QA agent must exercise
every criterion and report PASS/FAIL per ID.

---

### SETTINGS-LOAD
- GIVEN I am on any tab
- WHEN I click "Settings" in the sidebar
- THEN the Settings tab loads with PageTabs showing [AI Model | Appearance | Security |
  Developer | Data], the "AI Model" section is shown by default (or the last-used section),
  and there is no JS error or blank screen.

### SETTINGS-TAB-SWITCH
- GIVEN I am on Settings → AI Model
- WHEN I click each of the other tabs (Appearance, Security, Developer, Data) in turn
- THEN only that section's content is shown for each tab — no overlapping panels, no stacked
  forms, and no blank panels.

### SETTINGS-PROVIDER-ROW-EXPAND
- GIVEN the AI Model section shows an "AI providers" grid of provider rows (ProviderRow
  accordion)
- WHEN I click on a provider row (e.g. Anthropic) that is collapsed
- THEN it expands to reveal the API key field and model hints — NOT a silent noop or blank
  expansion.

### SETTINGS-PROVIDER-KEY-SAVE
- GIVEN a provider row is expanded and I enter a valid-format API key in the key field
- WHEN I click Save on that row
- THEN a "Saved ✓" flash appears next to the button AND the row shows "has_api_key: true"
  state (the key field clears / shows the "key saved" indicator).

### SETTINGS-DEFAULT-MODEL-SAVE-REFLECTS-IN-CHAT
- GIVEN I am on Settings → AI Model → Instance default panel
- WHEN I change the Model field to a different model (e.g. pick a different model from the
  hint chips) and click "Save default"
- THEN (1) a "Saved ✓" flash appears, AND (2) if I open (or look at) the chat panel's model
  selector, the "Default (…)" option now shows the new model name — not the old one.
  (Regression guard: this is the cost-bug class — Settings saves successfully but the chat
  selector must update via the MODEL_CONFIG_CHANGED_EVENT.)

### SETTINGS-APPEARANCE-ACCENT-PREVIEW
- GIVEN I am on Settings → Appearance
- WHEN I enter a valid hex color (e.g. #ff6b6b) in the "Accent color" field
- THEN a color swatch appears next to the field showing that color — AND clicking "Save
  appearance" applies the color live to the page's `--accent` CSS variable without requiring
  a reload.

### SETTINGS-APPEARANCE-INVALID-HEX
- GIVEN I am on Settings → Appearance
- WHEN I enter an invalid hex value (e.g. "notacolor") and click "Save appearance"
- THEN an inline error "Accent must be a hex color like #3b86dd (or empty to reset)" is
  shown and the form does NOT save.

### SETTINGS-SECURITY-CHANGE-PASSWORD
- GIVEN a password is configured (auth is required) and I am on Settings → Security
- WHEN I fill in the current password, a new password, and the confirmation, then click
  "Change password"
- THEN on success a "Password updated" message appears and the fields clear — NOT a silent
  failure or a blank page reload.

### SETTINGS-SECURITY-MISMATCH-ERROR
- GIVEN I am on Settings → Security
- WHEN I type a new password and a different string in "Confirm password", then click
  "Change password"
- THEN an inline error "Passwords do not match." is shown and the password is NOT changed.

### SETTINGS-DEVELOPER-MCP-PANEL
- GIVEN I am on Settings → Developer
- WHEN I view the section
- THEN the MCP access panel (API token, MCP endpoint) is visible with a "Copy" button — NOT
  a blank panel. Clicking Copy copies the endpoint to the clipboard without an error.

### SETTINGS-DATA-EXPORT
- GIVEN I am on Settings → Data
- WHEN I click "Export backup (.zip)"
- THEN a .zip file download starts (browser triggers the file save dialog) — NOT a 404 or
  silent failure.

### SETTINGS-DATA-IMPORT-PREVIEW
- GIVEN I am on Settings → Data and I have a valid export bundle .zip
- WHEN I select that file via "Choose import file…"
- THEN an import preview table is shown listing files, row counts, and integrity checks —
  NOT a blank panel or immediate import.

### SETTINGS-DATA-RESET-DOUBLE-CONFIRM
- GIVEN I am on Settings → Data
- WHEN I click "Reset instance / clear all data" and then click "Continue…" in the first
  confirm step
- THEN a second confirmation step appears with a "Yes, erase everything" button — the data
  is NOT wiped on the first click alone.

---

## AI spend panel (Settings → AI Model → AI spend)

### SETTINGS-SPEND-DEFAULT-TODAY
- GIVEN I am on Settings → AI Model and the "AI spend" panel is visible
- WHEN the panel first loads
- THEN the window segmented control has **"Today"** selected by default (not "Last 30 days"),
  and the figures shown are for the current local day — NOT a 30-day total.

### SETTINGS-SPEND-LOCAL-TIMEZONE
- GIVEN my browser is in a non-UTC zone (e.g. UTC+8) and a turn was recorded earlier today
  local time but in the previous UTC day
- WHEN I view the "Today" window
- THEN that turn's cost is counted under **today** (my local calendar day) — the boundary
  follows my timezone, not UTC. (The `/api/cost/summary` request carries a `tz=` parameter.)

### SETTINGS-SPEND-WINDOW-SWITCH
- GIVEN the AI spend panel is showing the Today window
- WHEN I select each of Yesterday, 7 days, 30 days, and All time
- THEN the totals, cost-trend timeline, and by-model / by-provider / by-surface breakdowns refresh
  for that window. The timeline must not include records outside the selected window.

### SETTINGS-SPEND-MOBILE-FILTER
- GIVEN the Settings viewport is phone-sized
- WHEN I view the AI spend panel
- THEN the five-option segmented control is replaced by a compact button labelled with the
  active window (for example "Today"); activating it opens a menu with Today, Yesterday,
  7 days, 30 days, and All time, and choosing an option updates the button and the data.

### SETTINGS-SPEND-EMPTY-STATE
- GIVEN no AI turns have been recorded for the selected window
- WHEN I view the AI spend panel
- THEN a "No spend recorded yet" empty state is shown — NOT a spinner that never resolves or a
  broken zero-row table.

### SETTINGS-SPEND-COST-TREND
- GIVEN one or more AI turns have been recorded in the selected window
- WHEN I view the AI spend panel
- THEN a compact timeline labelled "Cost trend" shows hourly buckets for Today/Yesterday and
  daily or adaptively-grouped buckets for longer windows, with visible average and peak dollar
  cost. The x-axis shows the start, midpoint, and end of the selected period; the strip is
  omitted when the selected window has no turns.

---

## Extensions (Settings → Developer → Extensions panel)

### SETTINGS-EXT-PHASE-NOTICE
- GIVEN I am on Settings → Developer
- WHEN I view the Extensions panel
- THEN a warn-bordered banner is visible stating "Only data_pack extensions can be installed
  in this version" — it is always shown regardless of whether any extensions are installed.

### SETTINGS-EXT-EMPTY-STATE
- GIVEN no extensions are installed
- WHEN I view Settings → Developer → Extensions
- THEN an "No extensions installed" empty state is shown with an "Install extension" button —
  NOT a blank area or spinner that never resolves.

### SETTINGS-EXT-LIST-BADGES
- GIVEN one or more extensions are installed
- WHEN I view the Extensions panel
- THEN each pack row shows: (a) a type badge ("data_pack"), (b) a source badge ("Shipped" or
  "User-installed"), and (c) a verification badge ("Verified" in green for registry-verified
  packs, "Not verified" in amber for unsigned/unverified packs). The "Verified" badge must
  come from the server field — the UI does NOT reconstruct it from manifest fields.

### SETTINGS-EXT-SHIPPED-UNINSTALL-DISABLED
- GIVEN a shipped extension is listed in the Extensions panel
- WHEN I view its row
- THEN the "Uninstall" button is disabled (greyed out) — NOT an active clickable button.
  Shipped packs cannot be removed via the UI.

### SETTINGS-EXT-INSTALL-OPENS-MODAL
- GIVEN I am on Settings → Developer → Extensions
- WHEN I click "Install extension"
- THEN a modal opens showing a segmented control with "Install from file" and "Install from
  URL" options, and an "Inspect" button — NOT a direct install without preview.

### SETTINGS-EXT-INSTALL-TWO-STEP-INSPECT
- GIVEN the install modal is open with "Install from URL" selected and a valid URL entered
- WHEN I click "Inspect"
- THEN the server's `/api/extensions/inspect` response is fetched (dry-run, no write), and
  on success the modal advances to the permission-review step showing the server-computed
  `permission_review` string verbatim in a `<pre>` block — NOT a loading spinner that never
  resolves, and NOT a client-reconstructed permission summary.

### SETTINGS-EXT-INSTALL-NOT-VERIFIED-CALLOUT
- GIVEN the install modal has advanced to the permission-review step
- WHEN I view the step
- THEN a warn-bordered callout is displayed reading "Not verified — install only if you trust
  the source" — always present in the review step regardless of the manifest's contents.

### SETTINGS-EXT-INSTALL-CONFIRM
- GIVEN the permission-review step is showing
- WHEN I click "Confirm install"
- THEN `/api/extensions/install` is called, and on success the modal shows "Installed
  successfully" — the extension is installed and the list refreshes after closing.

### SETTINGS-EXT-INSTALL-REFUSED-TYPE
- GIVEN the install modal is open and I provide a zip whose type is not data_pack
  (e.g. compute_plugin)
- WHEN I click "Inspect"
- THEN the server's error message (e.g. "compute_plugin type not supported in this version")
  is shown inline in the modal — the modal does NOT advance to the permission-review step.

### SETTINGS-EXT-UNINSTALL-CONFIRM
- GIVEN a user-installed extension is listed in the Extensions panel
- WHEN I click "Uninstall" and then "Uninstall" in the confirm modal
- THEN `DELETE /api/extensions/{type}/{id}` is called, and on success the extension is
  removed from the list — NOT silently kept or a page reload required.
