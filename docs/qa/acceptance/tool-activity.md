# Tool activity — acceptance criteria

The chat transcript's record of what Lucius actually did: a summary line, a rail of rows, and
the request/result behind each one. One renderer (`components/chat/toolActivity/`), reached
from every chat surface through `MessageBubble`.

**Surfaces to exercise:** the Lucius tab, the side panel, the floating dock, and an
Automation run opened from its run card. All four render the same transcript — a criterion
that passes in one and fails in another is a real bug, not a variant.

---

### TOOLACT-NO-RAW-NAMES
- GIVEN a conversation where Lucius called an external MCP tool (e.g. Playwright, IBKR)
- WHEN I read the transcript
- THEN no row, summary line, or tooltip shows a raw tool name — no `mcp__…`, no
  `snake_case`. Each row reads as a sentence ("Opened vnexpress.vn", "Took a screenshot").

### TOOLACT-OLD-TRANSCRIPT
- GIVEN a conversation recorded BEFORE this feature shipped
- WHEN I reopen it from the conversation list
- THEN its tool rows are labelled exactly like a fresh one (labels are resolved on read, not
  stored), with no raw names anywhere.

### TOOLACT-SUMMARY
- GIVEN a turn where Lucius made several DIFFERENT kinds of call
- WHEN the turn finishes
- THEN a summary line above the rows states the aggregate with real counts (e.g. "Opened 2
  pages, took 2 screenshots"), and the counts match the rows below.

### TOOLACT-SUMMARY-NOT-ECHOED
- GIVEN a turn whose calls all carry the same label
- WHEN I read the summary line
- THEN it does NOT repeat that label — it shows the step count and fold control only.

### TOOLACT-COLLAPSE
- GIVEN a turn with more than one step
- WHEN I click the summary line
- THEN every row folds away, leaving the summary and step count; clicking again restores
  them, AND any row I had expanded before folding is still expanded.

### TOOLACT-EXPAND
- GIVEN a completed tool row
- WHEN I click it
- THEN a Request box and a Result box appear beneath THAT row (and no other), with
  syntax-highlighted JSON, a byte size, and a copy button that puts the content on the
  clipboard.

### TOOLACT-KEYBOARD
- GIVEN focus is in the transcript
- WHEN I Tab to a tool row and press Enter or Space
- THEN it expands, shows a visible focus ring, and reports its expanded state to a screen
  reader (`aria-expanded`).

### TOOLACT-CLUSTER
- GIVEN Lucius called the same tool several times in a row
- WHEN I read the transcript
- THEN they appear as ONE row with a count ("Read 3 transactions"); expanding it lists the
  individual calls, and each of those expands to its own request/result.

### TOOLACT-ORDER
- GIVEN a turn that interleaved two kinds of call (navigate, screenshot, navigate, screenshot)
- WHEN I read the rows
- THEN they appear in the order they happened — four rows, not two merged ones — while the
  summary still aggregates them ("Opened 2 pages, took 2 screenshots").

### TOOLACT-FAILURE
- GIVEN a tool call that failed (e.g. an MCP call needing authentication)
- WHEN I read the transcript WITHOUT clicking anything
- THEN that row shows a warning icon and the error message in full, it is NOT merged into a
  neighbouring count, and the summary line ends with "· 1 failed".

### TOOLACT-FAILURE-EXPANDED
- GIVEN the failed row above
- WHEN I expand it
- THEN the result box is titled "Error" and is visually distinct from a successful result.

### TOOLACT-CONNECTOR-MARK
- GIVEN a call to a tool owned by a connector with a brand mark (IBKR, Playwright, Tavily)
- WHEN I read its row
- THEN the connector's logo appears in the icon column and its name appears as the
  breadcrumb; a tool owned by no connector shows a line icon and NO breadcrumb.

### TOOLACT-MCP-IMPERSONATION
- GIVEN an MCP server the operator named `stripe` whose endpoint is NOT Stripe's
- WHEN I read a row for one of its tools
- THEN it does NOT wear Stripe's logo (it falls back to the line icon).

### TOOLACT-BUSY
- GIVEN a turn currently running a tool
- WHEN I watch the transcript
- THEN the in-flight row shows its label with a busy indicator, and cannot be expanded until
  it has something to show.

### TOOLACT-THINKING
- GIVEN a turn where Lucius produced reasoning
- WHEN I read the transcript
- THEN the reasoning appears as a row on the SAME rail, with the Lucius sparkle icon,
  previewing at most two lines ending in an ellipsis.

### TOOLACT-THINKING-EXPAND
- GIVEN the thinking row above
- WHEN I expand it
- THEN it shows the full reasoning as formatted markdown, the header reads "Thought process",
  and the preview's two lines are NOT repeated above the full text.

### TOOLACT-DOCK-WIDTH
- GIVEN the floating dock (max 560px)
- WHEN a row's label, breadcrumb and detail together exceed the width
- THEN the text truncates with an ellipsis and the row stays on one line — the transcript
  never scrolls horizontally.

### TOOLACT-AUTOMATION-STEPS
- GIVEN an Automation run that called tools
- WHEN I open the run and read its step list
- THEN each step reads as the same sentence the chat transcript shows for that call — never
  "Mcp  playwright  browser navigate".
