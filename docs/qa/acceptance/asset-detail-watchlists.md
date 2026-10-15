# Acceptance — Asset Detail: Watchlists dropdown (replaces Follow)

The single `★ Following / ☆ Follow` toggle in the asset-detail header is replaced by a
**Watchlists** dropdown. The app supports many named watchlists, and a ticker can belong to
several at once, so the control must add/remove the asset **per list** — never a global delete.

Context: `frontend/src/components/intel/AssetWatchlistMenu.tsx` (the control) wired into
`frontend/src/components/intel/AssetDetailView.tsx`. Backend watchlist API unchanged.

### ADWL-OPEN
- GIVEN I am on an asset detail page for a ticker (e.g. NVDA)
- WHEN I click the **Watchlists** button in the header
- THEN an anchored dropdown opens listing **all** my watchlists, each as a checkbox row showing
  whether NVDA is in it, plus a **+ New watchlist** action

### ADWL-ADD
- GIVEN the dropdown is open and NVDA is **not** in "AI & semiconductors"
- WHEN I click that list's row
- THEN its checkbox fills and that list's count increments, and NVDA is added to **only** that
  list (`POST /api/watchlist/items` with `watchlist: <listId>`) — the other lists are untouched

### ADWL-REMOVE-NONDESTRUCTIVE
- GIVEN NVDA is in **both** "Core holdings" and "AI & semiconductors"
- WHEN I uncheck "Core holdings"
- THEN NVDA is removed from "Core holdings" **only**
  (`DELETE /api/watchlist/items/{id}?watchlist_id=<coreId>`) and **remains** in
  "AI & semiconductors" — it is never deleted out of every list at once

### ADWL-COUNT-BADGE
- GIVEN NVDA is in 2 watchlists
- WHEN I view the header
- THEN the Watchlists button shows a count badge **2**; when NVDA is in 0 lists the badge is hidden

### ADWL-NEW
- GIVEN the dropdown is open
- WHEN I click **+ New watchlist**
- THEN a new watchlist is created (`POST /api/watchlists`) and NVDA is added to it, and the new
  list appears in the dropdown already checked

### ADWL-CLOSE
- GIVEN the dropdown is open
- WHEN I click the backdrop outside it (or press Escape)
- THEN the dropdown closes; if membership changed, the asset detail does a GET-only refetch (the
  news/LLM brief is **not** re-run). Price alerts are **independent of watchlist membership** (ADR
  0014) — they live in the alert store keyed by ticker, so the Price-alerts card is **unchanged** by
  adding/removing the asset from a watchlist (removing it never deletes its alerts).

### ADWL-NO-FOLLOW
- GIVEN I am on any asset detail page
- THEN there is **no** single Follow/Following button — the Watchlists dropdown is the only
  watchlist control in the header

### ADWL-GATE
- GIVEN I am on an asset that has no ticker symbol
- THEN the Watchlists control is **not** rendered (same gate as the old Follow button)
