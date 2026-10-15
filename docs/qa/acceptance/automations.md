# Acceptance criteria — Automations tab

Interaction-level GIVEN/WHEN/THEN for the **Automations** tab (Rules + Scheduled Tasks +
Subscriptions). The QA agent must exercise every criterion and report PASS/FAIL per ID.

---

### AUTO-LOAD
- GIVEN I am on any tab
- WHEN I click "Automations" in the sidebar
- THEN the Automations tab loads with three PageTabs ("Rules", "Scheduled", and
  "Subscriptions") visible, no JS error, and no blank screen. The Rules tab is active by
  default (or whichever was last used, per localStorage).

### AUTO-TAB-SWITCH
- GIVEN I am on the Automations tab showing "Rules"
- WHEN I click the "Scheduled" tab
- THEN the scheduled tasks panel is shown AND the Rules panel is hidden — not both stacked.
  Clicking "Rules" again brings the rules panel back.

### AUTO-RULE-CREATE-OPEN
- GIVEN I am on Automations → Rules
- WHEN I click the button to create a new rule (e.g. "+ Add rule" or the equivalent)
- THEN the rule creation form/modal opens with empty fields for match conditions and set
  actions — it does NOT silently fail or navigate away.

### AUTO-RULE-CREATE-SAVE
- GIVEN the rule creation form is open with a valid description_contains match and a
  category set (e.g. match "Netflix" → set category "subscriptions")
- WHEN I click Save
- THEN the modal closes AND the new rule appears in the rules list with the correct
  match condition and category label shown.

### AUTO-RULE-EDIT
- GIVEN at least one rule exists in the list
- WHEN I click the edit action on that rule
- THEN the rule edit form opens pre-populated with that rule's current values — NOT blank
  fields.

### AUTO-RULE-DELETE-CONFIRM
- GIVEN at least one rule exists
- WHEN I click Delete on a rule and confirm the deletion in the confirmation step
- THEN the rule is removed from the list and is no longer applied to new transactions.

### AUTO-RULE-TEST-DRY-RUN
- GIVEN a rule with a description_contains matcher is open for editing or viewing
- WHEN I click "Test" (or equivalent dry-run control)
- THEN a result is shown indicating which existing transactions would match and whether the
  category would change (not a silent noop or an error page).

### AUTO-SCHED-CREATE-OPEN
- GIVEN I am on Automations → Scheduled
- WHEN I click "+ New scheduled task" (or equivalent)
- THEN the scheduled task creation form opens with action type, schedule type, and other
  fields available — it does NOT silently fail.

### AUTO-SCHED-CREATE-NOTIFY
- GIVEN the scheduled task form is open
- WHEN I select action "notify", fill in a message, pick a schedule (e.g. daily), and click
  Save
- THEN the modal closes AND the new task appears in the scheduled tasks list with the action
  and next-run time visible.

### AUTO-SCHED-RUN-NOW
- GIVEN a scheduled task exists in the list
- WHEN I click "Run now" on that task
- THEN the task executes immediately (a brief loading state) and the UI reflects the run
  (last run time updates or a result note appears) — no silent failure.

<!-- AUTO-SCHED-PREFILL-FROM-AGENTS and AUTO-RULES-EMPTY-STATE were removed 2026-08-02:
     the Agents tab and the Rules sub-tab no longer exist. Agents were retired (ADR
     2026-08-02-retire-agents.md) and rules became event-triggered Automations, so both
     criteria described a journey through screens that are gone. -->

### AUTO-RUN-PROGRESS-IS-THE-RUNS-OWN-PLAN
- GIVEN an Automation whose run declared tasks with `add_run_tasks`
- WHEN I open that run in History
- THEN Progress lists the run's OWN task subjects in plain language (not tool names), the
  in-progress one is visually distinct from the pending ones, and the count reads "N of M".

### AUTO-RUN-PROGRESS-EMPTY-STATE
- GIVEN a run that declared no tasks (a short run, or an older one)
- WHEN I open it
- THEN the Progress section still renders, showing linked placeholder marks and "Task
  progress appears here for longer runs" — never a missing section that shifts the card.

### AUTO-RUN-CONVERSATION-IS-REOPENABLE
- GIVEN a completed run
- WHEN I tap its History row
- THEN the Lucius panel opens THAT run's conversation, headed "Ran <automation name>", and
  the header links back to the run in the Automations tab.

### AUTO-RUN-APPROVAL-SURVIVES-RELOAD
- GIVEN a run parked `waiting` on an approval card
- WHEN I reload the page and reopen that run
- THEN the approval card is still there and still answerable — the run must not be
  resumable only from the tab that happened to be open when it stalled.

---

## Subscriptions sub-tab (AUTO-SUBS-*)

### AUTO-SUBS-TAB-LOAD
- GIVEN I am on the Automations tab
- WHEN I click the "Subscriptions" PageTab
- THEN the Subscriptions panel renders with a SummaryBar (Monthly total / Active / Dismissed),
  a "Recurring charges" Panel header, and a SegmentedControl [Active|All] — no JS error and
  no blank screen. The selected segment is "Active" by default.

### AUTO-SUBS-SHOWS-ROWS
- GIVEN the instance has transaction history that produces at least one detected subscription
- WHEN the Subscriptions panel loads
- THEN each detected subscription renders as a row containing: an avatar initial, the display
  name, a cadence Badge (Weekly/Monthly/Quarterly/Annual), the last charge amount, the
  next-expected date ("next DD MMM"), an occurrence count ("seen N×"), a category label, and
  a "/mo equiv" monthly-equivalent amount. The raw confidence float does NOT appear.

### AUTO-SUBS-TOTAL
- GIVEN the Subscriptions panel has loaded with at least one active subscription
- WHEN I view the SummaryBar
- THEN the "Monthly total" card shows the sum of monthly_equivalent for all non-dismissed
  subscriptions formatted in the base currency, the "Active" card shows the correct count,
  and the "Dismissed" card shows the correct count.

### AUTO-SUBS-CONFIRM
- GIVEN at least one subscription row is in "detected" status (has Confirm + Dismiss buttons)
- WHEN I click "Confirm" on that row
- THEN the row's status updates to "confirmed" (a "Confirmed" badge appears) and the row
  remains visible in Active mode — it is NOT dismissed.

### AUTO-SUBS-DISMISS
- GIVEN at least one subscription row is visible in Active mode
- WHEN I click "Dismiss" on a row
- THEN that row disappears from the Active view and the Dismissed count in the SummaryBar
  increments by 1 while the Active count decrements by 1.

### AUTO-SUBS-ALL-MODE
- GIVEN I have dismissed at least one subscription
- WHEN I switch the SegmentedControl to "All"
- THEN dismissed rows reappear with a "Dismissed" badge and a "Restore" button replacing the
  Confirm/Dismiss actions.

### AUTO-SUBS-RESTORE
- GIVEN I am in "All" mode and at least one row has status "dismissed"
- WHEN I click "Restore" on a dismissed row
- THEN that row's status resets to "detected" (Confirm + Dismiss buttons appear again) and the
  Dismissed count in the SummaryBar decrements.

### AUTO-SUBS-ANNUAL-SECTION
- GIVEN the transaction history includes a detected annual subscription
- WHEN the Subscriptions panel loads
- THEN annual subscriptions are NOT shown in the main list — they appear in a collapsed
  "Annual (N)" CollapsibleSection at the bottom. Expanding it reveals the annual rows.

### AUTO-SUBS-EMPTY
- GIVEN the instance has no transaction history that produces a detected subscription
- WHEN the Subscriptions panel loads
- THEN an EmptyState is shown with title "No recurring charges detected" and a hint about
  needing sufficient transaction history — not a blank panel, not an error.

### AUTO-SUBS-LUCIUS-ASK
- GIVEN the Subscriptions panel is loaded
- WHEN I click the "✦ Ask Lucius" button in the panel header
- THEN the chat panel opens (or becomes focused) with the text "Review my subscriptions and
  tell me if there's anything I should consider cancelling" pre-filled in the composer.

### AUTO-SUBS-LOADING
- GIVEN the subscriptions API call is in flight
- WHEN the panel is first mounting
- THEN a loading indicator with label "Detecting subscriptions…" is shown instead of a blank
  or broken layout.

### AUTO-SUBS-ERROR
- GIVEN the /api/subscriptions endpoint returns an error
- WHEN the panel tries to load
- THEN an ErrorState is shown with a "Retry" button — clicking it re-fetches the data.
