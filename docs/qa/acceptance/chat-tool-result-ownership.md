# Acceptance criteria — Chat tool-result ownership

Interaction-level GIVEN/WHEN/THEN for the expanded tool-call detail panel in the Chat
(Lucius) message list — CHAT-TOOL-RESULT-OWNERSHIP,
`.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md` §9. An expanded
panel must be visually anchored to exactly the call it belongs to: repeated name+status
header, a bounded structured summary by default, raw JSON only behind an explicit toggle,
and never spilling visual ownership onto a neighboring collapsed chip. The QA agent must
exercise every criterion and report PASS/FAIL per ID.

---

### TOOLRESULT-PANEL-OWNS-ITSELF
- GIVEN a Lucius reply whose action row shows several tool chips side by side, including a
  `list_statements` call and at least one of `get_transactions` / `find_tools` /
  `get_statement`
- WHEN I click the `list_statements` chip to expand it
- THEN its detail panel opens directly below that chip only, the panel's own header repeats
  "list_statements" and a status badge, and the panel visibly touches only the
  `list_statements` chip — no neighboring collapsed chip appears inside or behind the panel's
  border, whether the panel is short or tall enough to grow past the row's other chips.

### TOOLRESULT-EXPAND-WHILE-STREAMING
- GIVEN a tool call is still streaming/executing (its chip shows the animated loading dots)
- WHEN I click that chip to expand it
- THEN the panel opens showing a "Running…" badge and a "Waiting for result…" loading row —
  no structured summary and no "Raw result" toggle are shown until the call finishes.

### TOOLRESULT-EXPAND-ERRORED-CALL
- GIVEN a completed tool call whose result is an error (e.g. `get_statement` with an unknown
  statement id)
- WHEN I expand its chip
- THEN the panel header shows an "Error" badge and the body shows the plain-language error
  message — not a raw JSON dump.

### TOOLRESULT-EXPAND-EMPTY-RESULT
- GIVEN a completed tool call whose result is a valid, empty read (e.g. `list_statements`
  with no statements uploaded)
- WHEN I expand its chip
- THEN the panel header shows a "No results" badge and the body shows a short empty-state
  sentence (e.g. "No statements found.").

### TOOLRESULT-RAW-TOGGLE
- GIVEN an expanded panel for a completed, successful tool call
- WHEN I look at the panel before touching anything
- THEN no raw JSON is visible anywhere in the panel — only the structured summary (table,
  key/value rows, or list) and a "Raw result ▼" toggle button
- WHEN I click "Raw result ▼"
- THEN the raw params/result JSON appears below the toggle and the arrow flips to "▲"
- WHEN I click it again
- THEN the raw JSON collapses back out of view.

### TOOLRESULT-RUN-CODE-UNCHANGED
- GIVEN a completed `run_code` tool call
- WHEN I expand its chip
- THEN the existing script/result view (purpose, Python source, result) renders under the
  new repeated name+status header exactly as before — no raw JSON is shown by default here
  either, and the same "Raw result" toggle is available.

### TOOLRESULT-RESPONSIVE-WIDTHS
- GIVEN the chat panel is open with an expanded tool-result panel visible
- WHEN I resize the browser (or the chat panel width) across narrow (~360px chat panel),
  medium, and wide layouts
- THEN the detail panel's table/kv rows stay inside the panel with no horizontal overflow of
  the page, long cell values truncate with ellipsis rather than breaking layout, and the
  panel still visibly belongs only to its own chip at every width.
