# Acceptance criteria — Inbox tab

Interaction-level GIVEN/WHEN/THEN for the **Inbox** tab (aggregated items,
kind badges, mark-reviewed, dismiss, MCP proposal apply,
finding cards with full reports, grouped-by-kind sections with Show more).
The QA agent must exercise every criterion and report PASS/FAIL per ID.

### INBOX-LOAD
- GIVEN I am on any tab
- WHEN I click "Inbox" in the sidebar
- THEN the Inbox tab loads showing either a list of items or an
  "Inbox zero" empty state — no JS error, no blank screen.

### INBOX-EMPTY-UNREAD
- GIVEN there are no unread items
- WHEN I view the Inbox tab with the "Unread" filter active (default)
- THEN the empty state shows "Inbox zero" and the helpful note about future
  items appearing here — NOT a loading spinner or broken layout.

### INBOX-FILTER-ALL
- GIVEN there are read (older) items in the inbox
- WHEN I click "All" in the Unread/All SegmentedControl
- THEN previously read items also appear in the list (the count is ≥ the
  unread count).

### INBOX-KIND-BADGES
- GIVEN the inbox list contains items of different kinds (intel, finding,
  signal, mcp_proposal)
- WHEN I view the list
- THEN each item row shows a coloured kind badge matching its type (e.g.
  "Finding" in green, "Intel" in amber) — never a raw kind string without
  a badge.

### INBOX-ACTIONABLE-BANNER
- GIVEN there is an intel item with status "action_needed" or a new agent
  finding
- WHEN I view the inbox
- THEN the item row has a "Needs attention" badge AND is listed before
  non-actionable items (actionable items appear first).

### INBOX-FINDING-REPORT
- GIVEN there is an agent finding item in the inbox with a full markdown report
- WHEN I click "View full report" toggle on that item
- THEN the markdown report body expands below the summary, rendering headings,
  bold text, and tables correctly — NOT raw Markdown characters like `###` or
  `|---|`.

### INBOX-MARK-REVIEWED
- GIVEN there is a new agent finding in the inbox
- WHEN I click "Mark reviewed" on that finding row
- THEN the finding is removed from the "Unread" list (its status changes from
  "new" to "reviewed") — no page reload is required.

### INBOX-DISMISS-ITEM
- GIVEN there is at least one dismissable item (signal, intel, or finding)
- WHEN I click the "✕" or "Dismiss" button on that item
- THEN the item disappears from the list immediately without a page reload.

### INBOX-DISMISS-ALL
- GIVEN there are two or more items in the inbox
- WHEN I click "Dismiss all (N)" and confirm the confirmation dialog
- THEN all listed items are removed and the inbox shows the empty state.

### INBOX-MCP-APPLY
- GIVEN there is an MCP proposal item with status "pending"
- WHEN I click "Apply" on that item and the operation succeeds
- THEN the item is removed from the list (it is no longer pending).

### INBOX-MARK-ALL-READ
- GIVEN there are unread items in the inbox
- WHEN I click "Mark all read"
- THEN the "Mark all read" button disappears (no more unread items) and the
  unread count badge on the Inbox sidebar item drops to zero.

### INBOX-GROUPED-BY-KIND
- GIVEN the inbox contains items of at least two different kinds
- WHEN I view the inbox
- THEN items are grouped under section headers ("Findings", "Proposals",
  "Intel", "Signals") — each section has a Panel title with
  a count badge showing the number of items in that section; items of different
  kinds are never interleaved in the same section.

### INBOX-SECTION-EMPTY-HIDDEN
- GIVEN the inbox contains items of only one kind (e.g. only Findings)
- WHEN I view the inbox
- THEN only the non-empty section is rendered — sections with zero items have
  no header or empty placeholder visible.

### INBOX-SECTION-SHOW-MORE
- GIVEN a section contains more than 5 items
- WHEN I first view that section
- THEN only the first 5 items are visible and a "Show N more" button appears
  at the bottom of the section; clicking it reveals all items and the button
  changes to "Show less"; clicking again collapses back to 5.

### INBOX-SECTION-KIND-BADGE-TONE
- GIVEN items of all four kinds are present
- WHEN I view the section headers
- THEN each section header badge uses a distinct tone: Findings = green (pos),
  Proposals = amber (warn), Intel = amber (warn), Signals = neutral —
  consistent with the per-item kind badges.
