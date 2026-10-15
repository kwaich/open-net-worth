# Acceptance — Lucius landing screen's Activity panel

Design authority: this change (2026-08-20). Verify on the running app via `make qa-e2e` /
`make qa-cu` against an isolated QA server (8090–8099, `ONW_QA_MODE=1`) — never `:5174`/`:8080`
directly.

The Lucius tab's landing/empty-state screen (greeting + composer, `ChatPanel.tsx`'s `isHomeEmpty`
branch, shared by the desktop `LuciusHome` and the phone's `LuciusMobile`) previously surfaced
nothing about Automations in flight or coming up — a `waiting` run needing approval, or a run
mid-execution, was invisible unless the user happened to open the Automations tab. This is
**not** a new chat mode or a connector selector: it is a read-only accent built entirely from
existing Automations data (`GET /api/automations/activity` →
`services/automations/activity.py::automation_activity`), rendered by
`frontend/src/components/tabs/lucius/ActivityPanel.tsx`.

## AP1 — Visibility: only on the clean landing screen, and only when there's something to show

- **AP1.1** GIVEN the Lucius tab, no active conversation, and no automation runs/schedules WHEN
  the landing screen renders THEN neither an "Active" nor a "Scheduled" panel appears — no
  placeholder, no "Nothing here yet" message. Regression:
  `ActivityPanel.test.tsx` › "renders nothing when both Active and Scheduled are empty".
- **AP1.2** GIVEN at least one automation run is `waiting` or `running` WHEN the landing screen
  renders THEN an "Active" panel appears with a count badge.
- **AP1.3** GIVEN at least one `status="active"` automation has a future `next_run_at` WHEN the
  landing screen renders THEN a "Scheduled" panel appears with a count badge, independent of
  whether "Active" has anything.
- **AP1.4** GIVEN an open conversation with messages (not the empty landing state) THEN neither
  panel renders, regardless of Automation activity — this is a landing-screen accent only, never
  layered over a real conversation.
- **AP1.5** GIVEN the phone's Lucius tab (`LuciusMobile`) in the same empty/no-activity or
  empty/with-activity states THEN AP1.1–AP1.3 hold identically — both surfaces share the same
  `ChatPanel` `isHomeEmpty` branch and the same `ActivityPanel`.

## AP2 — Active section content and actions

- **AP2.1** GIVEN a `waiting` run WHEN its row renders THEN the subtitle reads
  "Permissions needed · {relative time}" and a **Review** button is shown.
- **AP2.2** GIVEN a `running` run WHEN its row renders THEN the subtitle reads
  "Running · {relative time}" and **no** Review button (or any button) is shown. Regression:
  `ActivityPanel.test.tsx` › "renders an Active section with a waiting row (Review shown) and a
  running row (no Review)".
- **AP2.3** GIVEN a `waiting` run's row WHEN the user clicks **Review** THEN the app navigates to
  the Automations tab with that automation's detail and that run selected
  (`?automation={id}&run={id}`), and clicking elsewhere on the row does the same. Regression:
  `ActivityPanel.test.tsx` › "clicking the Review button navigates with the automation and run
  id, without also firing the row click" (asserts exactly one navigation call).
- **AP2.4** GIVEN more than 5 active rows WHEN the panel renders THEN only 5 show, with a
  "Show N more" button; tapping it reveals the rest and the button becomes "Show less".
  Regression: `ActivityPanel.test.tsx` › "caps rows at 5 with a Show more / Show less toggle".
- **AP2.5** GIVEN runs across more than one automation THEN rows are ordered most-recently
  `started_at` first, regardless of which automation they belong to. Regression:
  `test_automation_activity.py::test_active_sorts_most_recently_started_first_across_automations`.
- **AP2.6** GIVEN a run that finished (`completed` or `failed`) within the last 4 hours THEN it
  still appears in Active, with a "Completed" / "Failed" subtitle using `finished_at` (not
  `started_at`) for the relative time, a green check / red alert dot, and no Review button.
  GIVEN a run that finished more than 4 hours ago THEN it does not appear. A `cancelled` run
  never appears regardless of recency. Regression:
  `test_automation_activity.py::test_active_includes_running_waiting_and_recently_finished`,
  `test_active_excludes_a_completed_run_just_past_the_recency_window`,
  `test_active_excludes_cancelled_runs_regardless_of_recency`;
  `ActivityPanel.test.tsx` › "renders recently-completed and recently-failed rows with no
  Review button, using finished_at".

## AP3 — Scheduled section content

- **AP3.1** GIVEN a scheduled automation's row WHEN it renders THEN the subtitle shows a
  day-relative time ("Today at HH:MM" / "Tomorrow at HH:MM" / "{Weekday}, {date} at HH:MM"), no
  action button, and no status dot in the waiting/running colors.
- **AP3.2** GIVEN a scheduled row WHEN the user clicks it THEN the app navigates to the
  Automations tab with that automation's detail open (no `run` param, since no run exists yet).
  Regression: `ActivityPanel.test.tsx` › "clicking a scheduled row navigates with just the
  automation id".
- **AP3.3** GIVEN an automation that is `paused`, has no `next_run_at`, or whose `next_run_at`
  has already passed THEN it never appears in Scheduled. Regression:
  `test_automation_activity.py::test_scheduled_excludes_paused_and_past_or_missing_next_run_at`.
- **AP3.4** GIVEN more than 5 scheduled rows THEN the same 5-row cap + "Show more"/"Show less"
  behavior as AP2.4 applies, and rows are ordered soonest `next_run_at` first. Regression:
  `test_automation_activity.py::test_scheduled_sorts_soonest_next_run_at_first`.

## AP4 — Backend contract

- **AP4.1** GIVEN `GET /api/automations/activity` WHEN called with no automations at all THEN it
  returns `200 {"active": [], "scheduled": []}`. Regression:
  `test_automations_api.py::test_get_activity_200_shape_when_empty`.
- **AP4.2** GIVEN seeded active + scheduled data WHEN the endpoint is called THEN each active row
  carries `automation_id`/`automation_name`/`run_id`/`status`/`started_at`, and each scheduled row
  carries `automation_id`/`automation_name`/`next_run_at` — the router does no business logic of
  its own, shaping only what `automation_activity()` returns. Regression:
  `test_automations_api.py::test_get_activity_returns_active_and_scheduled_rows`.
