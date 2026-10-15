# Acceptance — Budget lenses (Phase 0 + Phase 1)

> **Naming note (2026-08-23):** at the time this acceptance file was written, the engine id
> `"envelope"` meant zero-based budgeting. It has since been renamed `zero_based`
> (`services/budget_engines/zero_based.py`). A separate cash-envelope engine later took the
> `"envelope"` id and was removed on 2026-09-25 (ADR
> `2026-09-25-cash-envelopes-engine-is-removed`). Every "Envelope" below refers to the
> ORIGINAL (now zero-based) engine. See RFC Decision I.

**Feature**: pluggable budget engines — zero-based (home, unchanged) + 50/30/20 (derived, peek-only).
**RFC**: `.claude/rfcs/2026-08-22-pluggable-budget-engines.md`
**Design**: Claude Design project *Budget Lenses — multi-paradigm budgeting*.
**Scope**: Phase 0 (interface, invisible) + Phase 1 (50/30/20 peek lens). **Out of scope**: home-lens
switching (Phase 2), the envelope/caps engines + write verbs (Phase 3), lens-aware dashboards (Phase 4).

Status is earned by evidence: a named passing test + owner sign-off on real numbers.
"It renders" is not a pass — every control below is clicked/typed and its outcome asserted.

---

## A. The zero-based engine must not move (regression — highest priority)

| # | Given / When / Then | Evidence |
|---|---|---|
| A1 | GIVEN a seeded book, WHEN the zero-based engine's `fold()` runs, THEN every number it emits equals `planning.budget_month()`'s — headline == `rta`, group subtotals == `totals`, each row's assigned/activity/available == that row's values. | `tests/test_budget_engine_equivalence.py` |
| A2 | GIVEN the Plan → Budget tab, WHEN it renders with the lens switcher present and "Zero-based" selected, THEN Ready to Assign and every envelope figure is byte-identical to pre-change. | screenshot diff + `/api/planning/month` response diff |
| A3 | GIVEN `backend/services/planning.py`, WHEN this feature's diff is reviewed, THEN that file is **unmodified**. | `git diff --stat` shows no `planning.py` |
| A4 | GIVEN the existing budget test suite, WHEN run, THEN it passes unchanged. | `pytest -m "not integration" tests/` |

## B. Lens switcher

| # | Given / When / Then |
|---|---|
| B1 | GIVEN Plan → Budget is active, WHEN it renders, THEN a segmented pill control shows "Zero-based" (with a **Home** badge) and "50/30/20", with Zero-based selected. |
| B2 | GIVEN Plan → Cash Flow or Outlook, WHEN active, THEN **no** lens switcher renders. |
| B3 | GIVEN the switcher, WHEN the `ⓘ` is clicked, THEN a popover explains that peeking never changes envelopes or money; clicking elsewhere closes it. |
| B4 | GIVEN "50/30/20" is selected, WHEN the page is reloaded, THEN the lens resets to "Zero-based" (never persisted — a peek is transient). |
| B5 | GIVEN the Budget tab mounts, WHEN the user has not clicked "50/30/20" this session, THEN the 50/30/20 ViewModel is **not** fetched or folded (lazy — RFC open question #2). |
| B6 | GIVEN the switcher at 640 / 1024 / 1440px with the chat panel open and closed, WHEN rendered, THEN no horizontal overflow and nothing hidden behind the chat dock. |

## C. 50/30/20 — setup

| # | Given / When / Then |
|---|---|
| C1 | GIVEN 50/30/20 has never been configured, WHEN the user selects it, THEN the empty state renders with "Set up 50/30/20" and both CTAs — and **no** fabricated zero-value ViewModel. |
| C2 | GIVEN the empty state, WHEN "Draft my mapping" is clicked, THEN a loading state shows, and on resolve a review list renders with every category assigned Needs / Wants / Savings / Skip. |
| C3 | GIVEN the review list, WHEN a row's bucket is changed, THEN the footer recap updates immediately with **no** network call. |
| C4 | GIVEN the targets row, WHEN Needs=70 and Wants=40 are entered, THEN "Apply mapping" is disabled and "Needs + Wants can't exceed 100%." shows inline. |
| C5 | GIVEN a valid mapping, WHEN "Apply mapping" is clicked, THEN it persists, the pane shows the configured view, and leaving/returning to the lens does not re-show the empty state. |
| C6 | GIVEN the empty state, WHEN "Start blank instead" is clicked, THEN the review list opens with every category "Skip" and default 50/30 targets, and **no** model call is made. |
| C7 | GIVEN the draft could not reach the model, WHEN the review list renders, THEN it says the mapping is a name-based best guess — it never claims Lucius drafted it. |
| C8 | GIVEN an already-configured mapping, WHEN "Re-draft" is used, THEN changed rows are listed under "Changed (N)" and unchanged rows sit in a collapsed disclosure. |

## D. 50/30/20 — configured view

| # | Given / When / Then |
|---|---|
| D1 | GIVEN a configured mapping and a normal month, WHEN the view loads, THEN a savings-rate headline with a status badge renders, plus Needs / Wants / Savings each showing amount, percent-of-income, and a target-marked meter. |
| D2 | GIVEN the Savings bucket, WHEN it renders, THEN it shows the residual explainer and **no** category rows (savings is income − needs − wants, it owns no categories). |
| D3 | GIVEN Needs or Wants exceeds its target percent, WHEN its meter renders, THEN the tone is `warn` (amber) — not `bad` (red). Red is reserved for a genuinely bad outcome. |
| D4 | GIVEN spending in categories mapped to no bucket, WHEN the view renders, THEN an "Unmapped spending" panel shows the correct total and count with a "Map it" CTA. GIVEN none, THEN the panel does not render at all. |
| D5 | GIVEN uncategorized (`category = None`) spend, WHEN the unmapped panel renders, THEN it is reported distinctly from *mapped-but-skipped* spend, and "Review in Accounts" opens the register filtered to uncategorized expenses for that period. |
| D6 | GIVEN a category row, WHEN clicked, THEN it opens the same transaction drilldown envelope rows use, scoped to that category and period. |
| D7 | GIVEN a book in a non-base currency, WHEN the view renders, THEN every amount is base currency, server-computed — no client-side FX anywhere. |

## E. The states that usually get skipped

| # | Given / When / Then |
|---|---|
| E1 | GIVEN a month with **zero income**, WHEN the view renders, THEN the headline shows `—` with the no-income explanation, **no** bucket renders a percent or a meter, and no `NaN`, `Infinity`, or `0%` appears anywhere. |
| E2 | GIVEN a month where Needs + Wants exceed income (savings negative), WHEN the view renders, THEN the headline and Savings card read negative in `bad` tone, and the Savings meter shows an empty track with the negative flag — never a negative-width bar, never a crash. |
| E3 | GIVEN the ViewModel request is in flight, WHEN the pane first mounts, THEN a skeleton matching the real layout renders and nothing shifts when data lands. |
| E4 | GIVEN the ViewModel request fails, WHEN the pane renders, THEN a scoped error with Retry shows — and the Zero-based lens and the rest of Plan keep working. |
| E5 | GIVEN a book with no transactions at all, WHEN the lens renders, THEN it degrades to zeros/empty with no fabricated figures. |

## F. `Meter` primitive (absorption is a regression check, not new UX)

| # | Given / When / Then |
|---|---|
| F1 | GIVEN Overview's budget-vs-actual and movers widgets, WHEN screenshotted before/after the `Meter` swap, THEN bar heights, colors, and end treatment are visually unchanged. |
| F2 | GIVEN a Commitment's funding coverage bar, WHEN a segment is hovered, tapped, and keyboard-focused, THEN the sources popover opens with the same content as before. |
| F3 | GIVEN an envelope target ring (row-level and modal), WHEN rendered, THEN the percentage and completion checkmark are unchanged. |
| F4 | GIVEN `Meter`, WHEN inspected, THEN it exposes `role="progressbar"` with `aria-valuenow`/`aria-valuemin`/`aria-valuemax` and a required accessible label. |
| F5 | GIVEN a caller passes a negative ratio, WHEN `Meter` renders, THEN it clamps to an empty track with the negative flag — it never emits a negative width. |

## G. Lucius (AI) surface

| # | Given / When / Then | Evidence |
|---|---|---|
| G1 | GIVEN `list_budget_engines`, WHEN called, THEN it returns each engine's descriptor plus which is home and which are configured. | `tests/test_budget_engine_tools.py` |
| G2 | GIVEN `get_budget_view` with no `engine_id`, WHEN called, THEN it folds the **home** engine for the current month. | same |
| G3 | GIVEN `get_budget_view` with an unknown `engine_id`, WHEN called, THEN it raises a not-found error that **names the real engines** (recoverable in one round trip). | same |
| G4 | GIVEN `draft_budget_engine_config` on zero_based, WHEN called, THEN it returns "nothing to propose" — it does not raise, and it writes nothing. | same |
| G5 | GIVEN all three new tools, WHEN `tests/test_tool_domains.py` runs, THEN each is explicitly classified read-only. | `tests/test_tool_domains.py` |
| G6 | GIVEN the tool registry, WHEN `backend/scripts/tool_inventory.py --lint` runs, THEN it exits zero. | `make lint` |
| G7 | GIVEN the 24 pre-existing budget-domain tools, WHEN the diff is reviewed, THEN none is renamed or removed. | `git diff` + tool inventory diff |
| G8 | GIVEN any new tool, WHEN its schema is inspected, THEN it exposes **no** caller-chosen `fields`/`include`/projection parameter. | schema review |

## H. Storage, portability, checkpoints

| # | Given / When / Then | Evidence |
|---|---|---|
| H1 | GIVEN `budget_engine_state`, WHEN two engine ids are written for one book, THEN each round-trips independently and neither disturbs the other (**this is the property owner decision A depends on**). | `tests/db/test_budget_engine_state_store.py` |
| H2 | GIVEN a write to `budget_engine_state`, WHEN it lands, THEN the DB write hook fires with the correct before-image. | same |
| H3 | GIVEN a `/rewind` after a lens-config edit, WHEN it completes, THEN the config reverts (the table is checkpoint-**INCLUDED**). | checkpoint test |
| H4 | GIVEN an export/import round trip, WHEN it completes, THEN engine state survives (portable/BRIDGED). | portability test |
| H5 | GIVEN `/api/reset`, WHEN it runs, THEN `budget_engine_state` is wiped with its siblings. | reset test |
| H6 | GIVEN `alembic check`, WHEN run at revision 0049, THEN it is clean and the migration applies to a fresh isolated DB. | `alembic check` |

## I. Cross-cutting gates

| # | Requirement |
|---|---|
| I1 | `make check` green — lint (ruff + import-linter + tool inventory), types, tests, build. |
| I2 | `make ui-gate` clean; `make qa-e2e` green for every criterion touched above. |
| I3 | `npm run lint:classes` passes — **any new co-located CSS file must be registered in `frontend/scripts/check-css-classes.mjs`'s `CSS_FILES`**, which is an explicit allowlist, not auto-discovered. |
| I4 | Screenshot evidence at 640 / 1024 / 1440px, chat open and closed, for: switcher, empty, draft review, configured, no-income, negative-savings, loading, error. |
| I5 | Dark / warm / light themes render via existing tokens with no hardcoded colors. |
| I6 | No file over ~400 lines, no function over ~60, introduced by this change. |

---

## Owner sign-off (required — status is earned, not asserted)

- [ ] **A2 verified on a real production book**: Ready to Assign and envelope figures are unchanged after the switcher ships.
- [ ] **D1/D7 verified on the owner's real numbers**: the 50/30/20 split matches what the owner believes their month looked like.
- [ ] **E1/E2 verified**: a zero-income month and an overspent month both render sensibly on real data.
