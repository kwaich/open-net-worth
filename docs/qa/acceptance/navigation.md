# Acceptance criteria — Navigation and Router

Interaction-level GIVEN/WHEN/THEN for the **TanStack Router hash-based navigation**
system. The app now uses hash routing (`/#/cash`, `/#/intel`, `/#/asset/<id>`) instead
of query parameters. The QA agent must exercise every criterion and report PASS/FAIL
per ID.

### NAV-TAB-LOAD
- GIVEN I am on any tab
- WHEN I click a different tab in the left sidebar (e.g., "Overview", "Cash", "Intel")
- THEN the new tab loads without JS error, the content changes, and the **URL hash
  updates** to match the tab (e.g., `#/cash`, `#/intel`, `#/overview`).

### NAV-HASH-PERSISTS
- GIVEN I am viewing a tab (e.g., `#/cash`)
- WHEN I refresh the page (F5 or browser refresh button)
- THEN the same tab loads again; the URL remains at the hash I was on (e.g., `#/cash`).

### NAV-DEEP-LINK
- GIVEN I know the URL of a tab (e.g., `#/intel`)
- WHEN I manually navigate to that URL (paste into address bar or open a link to it)
- THEN the tab loads directly without needing to click the sidebar, and the content
  is correct.

### NAV-BACK-FORWARD-BASIC
- GIVEN I have visited at least two different tabs in sequence (e.g., Overview, then
  Cash, then Intel)
- WHEN I click the browser **Back** button
- THEN the URL hash steps back to the previous tab I was on, and the tab content
  re-renders correctly.

### NAV-BACK-FORWARD-CHAIN
- GIVEN I have visited a chain of at least 3 tabs in order (e.g., Overview → Cash →
  Intel → Commitments)
- WHEN I click browser **Back** multiple times
- THEN each click steps back one tab at a time in reverse order (Commitments → Intel
  → Cash → Overview), and the URL hash and page content match each step.

### NAV-FORWARD-RESTORES
- GIVEN I have gone Back through several tabs and am partway through the history (e.g.,
  at Cash after visiting Overview → Cash → Intel)
- WHEN I click the browser **Forward** button
- THEN the page advances to the next tab in the history (Intel), the URL hash updates,
  and the content renders correctly.

### NAV-ASSET-DETAIL-OPEN
- GIVEN I am on the Investments tab or any tab with asset cards
- WHEN I click on an asset/holding to open its detail view
- THEN the URL changes to a hash like `#/asset/<instrumentId>` (with optional search
  params), and the asset detail panel opens showing the asset's name, price, holdings
  info without JS error.

### NAV-ASSET-DETAIL-BACK
- GIVEN I am viewing an asset detail screen (URL = `#/asset/<id>`)
- WHEN I click the browser **Back** button
- THEN the URL returns to the previous tab (e.g., `#/investments`), the asset detail
  panel closes, and the parent tab content re-appears.

### NAV-ASSET-PEER-STACK
- GIVEN I am viewing an asset detail (e.g., `#/asset/AAPL`)
- WHEN I click on a **peer or related asset** in the detail view (if available) to open
  another asset detail
- THEN the URL updates to the new asset ID (e.g., `#/asset/MSFT`), the detail view
  shows the new asset, and the history stack grows (Back is available).

### NAV-ASSET-PEER-BACK-STEP
- GIVEN I have drilled into at least 2 asset details in sequence via peer clicks (e.g.,
  AAPL → MSFT → GOOGL)
- WHEN I click browser **Back**
- THEN the URL returns to the previous asset (MSFT), **not** the parent tab. Back
  again returns to AAPL. This confirms the peer stack is preserved in history.

### NAV-DRILL-IN-PAYEES
- GIVEN I am on the Cash tab or its sub-view
- WHEN I click a "Manage payees" or similar drill-in control
- THEN the URL changes to `#/cash/payees`, and the payees management interface loads.

### NAV-DRILL-IN-BACK-PAYEES
- GIVEN I am on the payees drill-in screen (`#/cash/payees`)
- WHEN I click the browser **Back** button
- THEN the URL returns to `#/cash`, the payees panel closes, and the parent Cash tab
  view re-appears.

### NAV-DRILL-IN-MONITORS
- GIVEN I am on the Intel tab or its sub-view
- WHEN I click a "Manage monitors" or "All" drill-in control
- THEN the URL changes to `#/intel/monitors`, and the monitors management interface
  loads.

### NAV-DRILL-IN-BACK-MONITORS
- GIVEN I am on the monitors drill-in screen (`#/intel/monitors`)
- WHEN I click the browser **Back** button
- THEN the URL returns to `#/intel`, the monitors panel closes, and the parent Intel
  tab view re-appears.

### NAV-SIDEBAR-HIGHLIGHT
- GIVEN I am on a tab (e.g., `#/cash`)
- WHEN I view the left sidebar
- THEN the sidebar shows the active tab visually highlighted or marked (e.g., bold,
  background color, or underline on "Cash").

### NAV-SIDEBAR-HIGHLIGHT-ASSET
- GIVEN I am viewing an asset detail (`#/asset/<id>`)
- WHEN I view the left sidebar
- THEN the sidebar shows the **source tab** highlighted (e.g., "Investments" if I
  opened the asset from the Investments tab), and the asset detail does not have its
  own sidebar entry, confirming it is a modal/detail view, not a full-tab navigation.

### NAV-COMMAND-PALETTE
- GIVEN the Command Palette is available (e.g., ⌘K on macOS)
- WHEN I use the command palette to navigate to a different tab
- THEN the URL hash updates correctly and the tab loads (if the palette supports
  navigation commands).

### NAV-CHAT-OVERLAY-PERSIST
- GIVEN I am on any tab (e.g., `#/cash`)
- WHEN I click to open the chat panel (if available)
- THEN the URL and tab content remain unchanged; the chat panel overlays on top.

### NAV-PRIVACY-TOGGLE
- GIVEN I am on any tab
- WHEN I click the privacy toggle (e.g., "Hide amounts" button) in the header
- THEN the amounts on the page mask/unmask, the URL hash does not change, and I
  remain on the same tab.

### NAV-NO-JS-ERROR
- GIVEN I perform any of the navigation actions above (click tabs, open assets, go
  Back/Forward, drill in/out)
- WHEN I observe the browser console (via DevTools or the harness screenshot)
- THEN no JavaScript errors are logged; all navigation transitions complete without
  exceptions.
