# Acceptance criteria — Connectors tab

Interaction-level GIVEN/WHEN/THEN for the **Connectors** tab. The QA agent must exercise
every criterion and report PASS/FAIL per ID.

---

### CONN-LOAD
- GIVEN I am on any tab
- WHEN I click "Connectors" in the sidebar
- THEN the Connectors tab loads showing a segmented filter (All / Channels / Data / Tools),
  a search box, a "+ Browse & add" button, and a sectioned card grid — with no JS error and
  no blank screen.

### CONN-SECTION-FILTER
- GIVEN the Connectors tab is showing the "All" view with cards in multiple sections
  (Channels, Data sources, Tools)
- WHEN I click "Channels" in the segmented filter
- THEN only the Channels section is shown; the Data and Tools sections disappear — no
  full page reload.

### CONN-SEARCH-FILTERS
- GIVEN the Connectors tab is visible with multiple connectors
- WHEN I type a connector name (e.g. "Telegram") in the search box
- THEN only connectors whose name or provider kind matches the search are shown; non-matching
  cards disappear in real time.

### CONN-SEARCH-EMPTY
- GIVEN I have typed a search term that matches no connector
- WHEN the filter resolves
- THEN an empty state "No connectors match…" is shown with a "Browse & add" action — NOT a
  blank panel or JS error.

### CONN-DETAIL-OPENS
- GIVEN any connector card is visible (e.g. Telegram, Webhook, Plaid)
- WHEN I click the "Details" button on that card
- THEN the ConnectorDetailModal opens showing: provider icon, name, health status badge,
  short description, capabilities badges, and (for MCP connectors) a "Tools N" chip that
  expands to tool descriptions. The modal is NOT blank.

### CONN-CONFIG-MODAL-OPENS
- GIVEN any connector that is not yet configured (e.g. Telegram, Plaid, Yodlee, IBKR)
- WHEN I click "Configure" (or the equivalent button) on that card
- THEN the per-provider configuration modal opens with the relevant fields (bot token, API
  key, etc.) visible and editable — NOT a blank modal, NOT a silent noop.
  (Regression guard for the TAX-CONFIG-OPEN class of bug: the Configure button must
  actually open the form.)

### CONN-ENABLE-TOGGLE-CONFIGURED
- GIVEN a connector that is configured but currently disabled
- WHEN I toggle the enable switch on its card (visible only after the connector is configured)
- THEN the card's status badge changes to "Enabled" AND the connector becomes active per
  GET /api/connectors (status field updates).

### CONN-NEEDS-ATTENTION-STRIP
- GIVEN at least one connector is in an error or degraded state
- WHEN I view the Connectors tab
- THEN a "Needs attention" strip appears at the top listing the names and status labels of
  affected connectors — clicking one of those names scrolls to and opens that connector's
  card.

### CONN-BROWSE-ADD-DIRECTORY
- GIVEN I am on the Connectors tab
- WHEN I click "+ Browse & add"
- THEN the DirectoryModal opens showing available connector types — NOT a blank modal or
  error.

### CONN-SECTION-ADD-CARD
- GIVEN at least one section (Channels / Data sources / Tools) is visible in the grid
- WHEN I see the ghost "Add a channel" / "Add a data source" / "Add an MCP server" card at
  the end of each section
- WHEN I click it
- THEN the Browse & add Directory opens pre-filtered to that section's family (e.g. Channels
  section opens the Channels filter in the directory).

### CONN-MCP-SERVER-ADD
- GIVEN I have opened the Browse & add directory and selected "Add an MCP server"
- WHEN the McpServerFormModal is open and I fill in a server name and URL, then click Save
- THEN the modal closes and a new MCP server card appears in the Tools section.

### CONN-IBKR-CONFIG-RENDERS
- GIVEN I click Configure on the IBKR connector card
- THEN the IBKR configuration modal opens with fields for Flex Token and Query ID visible —
  NOT a blank modal. (Specific regression guard: Yodlee, IBKR, Alpaca connectors must render
  their config sections, not an empty modal body.)
