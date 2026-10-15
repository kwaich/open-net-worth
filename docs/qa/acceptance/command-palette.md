# Acceptance criteria — Command Palette omnibox (Markets + Ask Lucius)

Interaction-level GIVEN/WHEN/THEN for the Command+K palette's two new tiers: **Markets**
(remote symbol search for assets the user does NOT hold) and **Ask Lucius** (a catch-all
that seeds the query into chat). The QA agent must exercise every criterion against the
RUNNING app and report PASS/FAIL per ID. Open the palette with ⌘K / Ctrl+K.

Design: `docs/superpowers/specs/2026-06-20-omnibox-command-palette-design.md` · ADR 0013.

### CMDK-OPEN
- GIVEN I am anywhere in the app
- WHEN I press ⌘K (or Ctrl+K)
- THEN the command palette opens with the input focused and the placeholder reads
  "Search or ask Lucius…".

### CMDK-INAPP-UNCHANGED
- GIVEN the palette is open
- WHEN I type the name of a tab, account, holding, or category I already have (e.g. an
  existing account name)
- THEN the existing in-app groups (Quick actions / Tabs / Accounts / Holdings / Categories)
  still appear and behave exactly as before, and selecting one navigates/filters as before.

### CMDK-MARKETS-APPEARS
- GIVEN the palette is open
- WHEN I type the name of a stock I do NOT hold (e.g. "apple") and pause briefly
- THEN a "Markets" group appears below the in-app groups containing candidate rows (e.g.
  "Apple Inc." with an "Equity" tag, the symbol "AAPL", and an exchange), each visibly
  marked as a market asset (not a held holding).

### CMDK-MARKETS-LOADING
- GIVEN I have typed ≥2 characters that trigger a market search
- WHEN the request is in flight (within ~250ms of typing)
- THEN a single non-selectable "Searching the market…" row shows under the Markets header,
  and it cannot be reached by arrow keys or activated by Enter.

### CMDK-MARKETS-CRYPTO
- GIVEN the palette is open
- WHEN I type a coin I do NOT hold (e.g. "solana" or "btc")
- THEN a Markets row for the coin appears with a "Crypto" tag and the correct symbol.

### CMDK-MARKETS-METAL
- GIVEN the palette is open
- WHEN I type "gold"
- THEN a Markets row for gold appears with a "Commodity" tag.

### CMDK-MARKETS-OPEN-NONHELD
- GIVEN a Markets row is shown for an asset I do NOT hold
- WHEN I select it (Enter or click)
- THEN the palette closes and the AssetDetail view opens at `#/asset/<instrumentId>`,
  showing the asset's NAME (e.g. "Apple Inc.", not just "AAPL"), price, news/analysis, and
  NO "Your position" card (because I don't hold it), without a JS error.

### CMDK-MARKETS-DEDUP
- GIVEN I hold an asset (e.g. it appears in the Holdings group)
- WHEN I type its name/symbol
- THEN it appears in Holdings and is NOT duplicated as a Markets row.

### CMDK-MARKETS-ERROR-SOFT
- GIVEN a market search request fails or returns nothing
- WHEN the request completes
- THEN no Markets group is shown (silent degrade), no error is thrown, and the Ask Lucius
  row still guarantees the result list is non-empty.

### CMDK-ASK-PRESENT
- GIVEN the palette is open
- WHEN I type any non-empty query
- THEN an `Ask Lucius: "<query>"` row is present (pinned, visually distinct with an accent
  icon and a separator above it).

### CMDK-ASK-DEFAULT-QUESTION
- GIVEN the palette is open
- WHEN I type a question-shaped query (ends with "?", ≥4 words, or starts with a question
  word — e.g. "how much did I spend on coffee in May")
- THEN the Ask Lucius row is the default-highlighted row, and pressing Enter opens the
  Lucius chat with that query seeded in the composer (NOT auto-sent).

### CMDK-ASK-NOT-STEAL
- GIVEN I type a query with a strong structured match (e.g. an exact tab name like
  "overview" or a held ticker)
- WHEN I look at the highlighted row
- THEN the structured match keeps the default highlight (Enter opens it), and Ask Lucius is
  pinned at the bottom rather than stealing Enter.

### CMDK-ASK-SEED-NOT-SEND
- GIVEN I select the Ask Lucius row
- WHEN the chat opens
- THEN the query text is seeded into the composer and focused, and NO message is sent until
  I press Send myself (the LLM does not fire automatically).

### CMDK-ARROW-NAV-STABLE
- GIVEN I typed a query and used the arrow keys to move the selection
- WHEN late market results arrive (the list grows)
- THEN my selected row does NOT jump/reset — the selection I navigated to is preserved.

### CMDK-EMPTY-QUERY
- GIVEN the palette is open with an empty input
- WHEN I have typed nothing
- THEN neither a Markets group nor an Ask Lucius row is shown (the empty palette is
  unchanged from before this feature).
