# Acceptance criteria — Investments / Lucius dock (Wave 2)

Interaction-level GIVEN/WHEN/THEN for the **Lucius floating dock** on the Investments tab:
per-sub-tab contextual prompts and placeholder text, hide-while-drawer-open and
hide-while-empty behavior, correct funnel into the existing chat pipeline, and coexistence
with the masthead chat button and the side panel.

The QA agent must exercise every criterion and report PASS/FAIL per ID. Run against a QA
instance on ports 8090–8099 with `ONW_QA_MODE=1`.

---

## Dock presence and per-sub-tab prompts

### INV-L-DOCK-VISIBLE-HOLDINGS
- GIVEN I am on the Investments tab with the Holdings sub-tab active
- WHEN the sub-tab loads with at least one position
- THEN a Lucius floating dock is visible (a persistent button or compact bar, distinct
  from the global masthead chat button) showing a Holdings-specific prompt or label
  (e.g. "Ask about your holdings", "Holdings analysis", or similar).

### INV-L-DOCK-VISIBLE-PERFORMANCE
- GIVEN I switch to the Performance sub-tab
- WHEN the sub-tab loads
- THEN the Lucius dock updates to show a Performance-specific prompt (e.g. "Ask about
  your returns", "Explain my risk metrics") — it does NOT retain the Holdings prompt.

### INV-L-DOCK-VISIBLE-ACTIVITY
- GIVEN I switch to the Activity sub-tab
- WHEN the sub-tab loads
- THEN the Lucius dock updates to show an Activity-specific prompt (e.g. "Summarize
  recent trades", "What did I buy this month?").

### INV-L-DOCK-VISIBLE-OPTIONS
- GIVEN I switch to the Options sub-tab
- WHEN the sub-tab loads
- THEN the Lucius dock updates to show an Options-specific prompt (e.g. "Review my
  options positions", "Which options are expiring soon?").

### INV-L-DOCK-PROMPT-PLACEHOLDER
- GIVEN the Lucius dock is visible on any Investments sub-tab
- WHEN I click or focus the dock input area
- THEN the placeholder text is sub-tab-specific (matching the sub-tab's prompt, e.g.
  Holdings shows "Ask about your holdings…") — it is NOT a generic "Ask Lucius…"
  placeholder on every sub-tab.

---

## Hide-while-drawer-open

### INV-L-DOCK-HIDES-DRAWER
- GIVEN the Lucius dock is visible on the Holdings sub-tab
- WHEN I open the chat side panel / drawer (via the masthead chat button or the dock itself)
- THEN the floating dock disappears (is hidden or unmounted) while the drawer is open —
  the dock does NOT overlap or stack on top of the open chat drawer.

### INV-L-DOCK-RESTORES-AFTER-DRAWER-CLOSE
- GIVEN the chat drawer was open (hiding the dock)
- WHEN I close the drawer
- THEN the Lucius dock reappears in its previous position for the active sub-tab.

### INV-L-DOCK-HIDES-MODAL
- GIVEN the Lucius dock is visible
- WHEN a modal opens (e.g. a DisposalForm or RecordDividendForm)
- THEN the dock is hidden behind the modal overlay — it does NOT float above the modal
  or intercept modal interactions.

---

## Hide-while-empty

### INV-L-DOCK-HIDDEN-NO-POSITIONS
- GIVEN no investment positions exist (fresh install or all removed)
- WHEN I navigate to any Investments sub-tab
- THEN the Lucius dock is NOT shown — there is no empty dock or dock with a disabled
  state visible to the user.

### INV-L-DOCK-HIDDEN-LOADING
- GIVEN the sub-tab is loading its data (request in-flight)
- WHEN the dock would normally appear
- THEN the dock does NOT flash briefly while loading — it only appears after data has
  loaded and confirms there is meaningful content to discuss.

---

## Funnel into existing chat pipeline

### INV-L-DOCK-OPENS-CHAT
- GIVEN the Lucius dock is visible on the Holdings sub-tab
- WHEN I click the dock (the main button or the prompt area)
- THEN the chat side panel opens; the sub-tab context (e.g. "Holdings") is pre-populated
  as context in the chat input or system message — it opens the SAME chat panel used
  by the masthead button, not a separate in-page chat widget.

### INV-L-DOCK-PREFILL-PROMPT
- GIVEN I click the dock on the Performance sub-tab
- WHEN the chat panel opens
- THEN the chat input is pre-filled with the sub-tab's suggested prompt (e.g. "Explain
  my risk-adjusted metrics") OR the prompt is submitted automatically as the first message,
  so the user lands in a relevant conversation — they do NOT land in a blank new chat
  with no context.

### INV-L-DOCK-SAME-SESSION
- GIVEN a chat session is already in progress in the side panel
- WHEN I click the Lucius dock
- THEN the chat panel opens to the existing session (not a new blank session) — the dock
  does NOT start a new session that discards previous conversation context, unless the
  user explicitly started a new chat.

### INV-L-DOCK-SUBMIT-SENDS
- GIVEN the chat panel is open via the Lucius dock
- WHEN I type a message and press Enter (or click Send)
- THEN the message is sent to Lucius and a response is received in the same panel —
  the dock's funnel does NOT break the chat submission pathway.

---

## Coexistence with masthead button and side panel

### INV-L-DOCK-COEXISTS-MASTHEAD
- GIVEN the Lucius dock is visible on the Investments tab
- WHEN I look at the page header area
- THEN the global masthead chat button (e.g. the "Chat with Lucius" button in the app
  header) is ALSO visible and functional — the dock does NOT replace or hide the masthead
  button; both are accessible.

### INV-L-DOCK-MASTHEAD-OPENS-SAME-PANEL
- GIVEN both the dock and the masthead button are visible
- WHEN I click the masthead button (not the dock)
- THEN the same chat side panel opens, not a second separate panel; both affordances
  open exactly one panel.

### INV-L-DOCK-POSITION-NO-OVERLAP
- GIVEN the chat side panel is closed
- WHEN I view the Holdings sub-tab with the dock visible and the full holdings table
- THEN the dock does not obscure any interactive element in the table (no row action,
  no scroll affordance, no pagination control is hidden behind the dock).

### INV-L-DOCK-POSITION-PANEL-OPEN
- GIVEN the chat side panel is open
- WHEN I view the Investments tab with the side panel visible
- THEN the dock is hidden (per INV-L-DOCK-HIDES-DRAWER) and the side panel occupies
  its expected position without layout breaking — the Holdings table, cockpit, and all
  sub-tab controls remain usable in the narrowed content area.

### INV-L-DOCK-NO-DUPLICATE-CHAT
- GIVEN the dock and masthead button both exist
- WHEN I open the chat via either affordance
- THEN only ONE chat panel is rendered in the DOM — there is no stacking of two panels
  or two distinct chat state instances.

---

## Empty / loading / error states

### INV-L-DOCK-ERROR-CHAT-UNAVAILABLE
- GIVEN the chat backend is unreachable (e.g. no API key configured)
- WHEN I click the Lucius dock
- THEN the chat panel opens and shows the existing "no key / unavailable" state message —
  the dock does NOT silently fail to open, and it does NOT show a raw network error.
