# 2026-07-19 — Bound tool results AT THE TOOL: filter first, cap second, decay third

**Status:** accepted (owner-directed, 2026-07-19)
**Where:** `backend/ai/tools/_bounded.py` (NEW — the shared cap/disclosure helpers),
`backend/ai/tools/ledger.py` + `backend/ai/tools/_txn_schema.py` (`get_transactions`:
default limit, `summary_only`, plural filters, `q_field`),
`backend/ai/tools/accounts.py` (`list_accounts` filters),
`backend/ai/tools/portfolio.py` (`get_portfolio` filters),
`backend/ai/tools/lots.py` (`get_lots` cap + offset + full-set totals),
`backend/ai/tools/agents.py` + `backend/ai/tools/skills.py` (roster body elision),
`backend/ai/tools/watchlist.py` (`get_signals` total), `backend/services/accounts.py`
(`filter_accounts`), `backend/services/ledger.py` (plural filters, `txn_matches_q`),
`backend/db/signals_store.py` + `backend/db/agent_finding_store.py` (`count`/`offset`),
`backend/services/options.py` (open-only bug), `backend/ai/assistant.py` (age decay),
`tests/test_cost_diet_tools.py`, `tests/test_cost_caching.py`

## Context — measured, and it settles a design question

Context is ~88% of this app's model bill; output is 7–12%. Within a turn's ~40,000 tokens,
history + tool results is ~20,000 — the largest and least managed slice. Transcript bytes are
**48% tool_results**, against 7% assistant prose and 2% user text.

Forensics over 289 real sessions:

- **83 of 144 results over 20K chars came from tools that ARE in `_SHRINKABLE_TOOL_NAMES`
  but were never called again**, so the supersession shrink never fired — it only stubs a
  result when the SAME tool is called again later in the window.
- 58 of 79 read tools are not in the shrink list at all.
- `MAX_TOOL_RESULT_CHARS` applied only to `web_search`/`fetch_page`. Every structured read
  was exempt.
- `list_accounts`: 169 calls, median 7.8 KB, **1.23 MB total**, 63 byte-identical repeats
  within a session — and it accepted **no parameters at all**, so every call dumped
  every account.
- `get_transactions` at its default limit of 100 measured ~15,175 tokens.

**A separate audit settled that compaction is NOT the answer**: context folding saves zero
tokens (the 30-message window truncates regardless, and the summary is prepended on top),
tool results are structurally EXCLUDED from the summariser (it keeps only `type=="text"`
blocks), the median session is 8 messages against a >40 fold threshold, and total fold spend
was $0.69 of $67.11. Bound the output instead — which is what Claude Code does (`Read` has
offset/limit, `Grep` has head_limit; it never lets a huge result in).

## Decision

### 1. Filter before you cap

**A cap returns an arbitrary prefix and can cut off exactly the row the caller needed, with
no way for it to know. A filter returns the right row.** So: cap only where the caller
genuinely cannot name what it wants (a transaction range, a live signal feed); filter
wherever it can.

- `list_accounts` gains `query` (substring over name/platform), `ids` (id OR name),
  `kind`, `type`, `currency`, `platform`. A one-account filtered call is ~250 bytes against
  ~7,800.
- `get_portfolio` gains `query`, `asset_class`, `account_id`, `bucket`. An UNFILTERED call
  keeps its historical bare-list shape and full coverage — the filters are strictly opt-in,
  because an unfiltered `get_portfolio` is the correct call for a portfolio total.
- `get_transactions` gains PLURAL filters (`account_ids`, and list forms of `category`,
  `transaction_type`, `tag`, `payee_id`) plus `q_field`. "What did I spend across my three
  cards" was three calls, each re-sending the whole conversation; it is now one.

### 2. Every bounded read discloses itself

Non-negotiable, because a model that does not know it got a partial answer will confidently
report a wrong total — which in this app is worse than a missing answer. Every capped or
filtered read reports `showing` + `total`, and a truncated one carries a note naming the true
total and a concrete next step.

Three specific hazards are called out in the note text itself:

- **A zero-match must never read as an empty database.** "0 of 31 accounts match — the
  accounts DO exist, your filter excluded them… Do NOT report that the user has no accounts."
- **A partial page must never be totalled.** `get_transactions`' page note points at the
  `summary` block (which is exact) instead.
- **A truncated lot list is a tax-reporting hazard**, not a UX annoyance: it produces both a
  wrong realized gain and a wrong long/short-term split. `get_lots` therefore ships a
  `totals` block computed over ALL lots, plus `offset` so every lot is reachable.

### 3. `summary_only` is safe; a caller-chosen `fields` list is not

`get_transactions` gains `summary_only`, which is **exact, not an approximation** — the
service computes its money totals over the full filtered set before paging
(`_txn_base_totals`). It is server-chosen, so it cannot hide a field the caller needed.

We deliberately did **NOT** add a free-form `fields`/projection parameter to any
money-bearing read. It would let the model select `amount` over `base_amount` and silently
defeat the historical-rate lock. `_project_txn` and `_project_position` are both deliberately
`return dict(t)` after a 2026-07-09 incident where a curated allowlist hid
ticker/quantity/last_four from every agent reading the ledger. **Row selection is safe; field
projection is not.** `_bounded.py`'s module docstring carries this rule so the next author
meets it before adding one.

### 4. Age decay runs AFTER supersession, not before

A tool result older than the decay boundary is stubbed regardless of its tool name and
regardless of whether that tool ever ran again — the catch-all for the 83-of-144 case the
supersession shrink misses. The stub carries the tool name AND its arguments, so the elided
result stays re-fetchable rather than merely gone.

Two non-obvious choices:

- **The boundary is an absolute history index quantised on `_WINDOW_SLIDE_CHUNK`**, not an
  offset from the end. A boundary computed relative to the end moves on EVERY turn, rewriting
  already-cached bytes every call and re-sending the whole prefix at the 1.25× cache-write
  rate — the exact failure `_WINDOW_SLIDE_CHUNK` exists to prevent, and it would have cost
  more than the decay saves. It is also pinned per-turn alongside `window_start` and
  `keep_full_ids`.
- **Order is smallest-stub-wins.** Running decay FIRST was measurably wrong: for a
  repeatedly-called shrinkable tool it replaced the short supersession stub with the longer
  args-carrying decay stub, growing the transcript ~200 bytes per stubbed result. Caught by
  measurement, not review. `_compact_tool_result` now checks supersession first and guards
  unconditionally that a stub is never longer than what it replaces.

Threshold `_AGE_DECAY_AFTER_MESSAGES = 12` (~6 turns) is chosen from the real distribution:
the median session is 8 messages, so decay never fires for a typical session and only bites
the long ones that dominate the bill.

### 5. `anyOf` over twin plural parameters

The plural filters are expressed as `anyOf: [string, array]` on the existing parameter rather
than as new `categories`/`tags`/`payee_ids` siblings. Twin parameters would have added ~400
chars to the ALWAYS-ON core tool offer on every turn — self-defeating in a change whose whole
purpose is reducing per-turn context. The singular shapes keep working, because stored skills
and agent playbooks reference them and a silently-dropped filter returns plausible rows for
the wrong question. `tests/test_txn_schema.py` was updated to accept `anyOf` as a complete
type declaration (every branch must still carry its own `type`).

## Two bugs found and fixed along the way

1. **`services/options.py::summary` shipped closed contracts.** `holdings.append` and the
   uncovered-call tally sat OUTSIDE the `if status == "open"` guard that protected the
   mark/bucket/collateral folds, so the lens returned every dead contract a book had ever
   recorded and counted them as uncovered — while its own docstring promised open-only.
   Net worth is unaffected (`_compute_options_mark_base` reads only `total_mark_base`, which
   was already open-guarded).
2. **`q_field='description'` and `q_field='memo'` were indistinguishable.** Both fell into a
   single branch searching BOTH fields, so the scoping the ADR
   `2026-07-17-split-note-into-description-and-memo` created could not actually be expressed.
   Now each scopes to the one field named; `note` remains the pre-split alias spanning both
   (which is what the register's `note:` search token sends). No UI sent `description`/`memo`,
   so this costs nothing.

The free-text matcher moved from `api/ledger.py` into `services/ledger.py` so the REST route
and its service twin share ONE implementation — a search that scoped differently depending on
which door you came through would be a silent data discrepancy. Direction respects the
dependency rule (`api → services`); all 5 import-linter contracts stay green.

## Measured result

A realistic 10-turn session (18 accounts, 320 transactions, 6 agents), run against HEAD and
against this change with an isolated data dir:

| Scenario | Tool-result bytes | Δ |
|---|---|---|
| HEAD | 295,177 | — |
| Bounded, **same calls** (no behaviour change) | 91,678 | **−68.9%** |
| Bounded, model **uses the new filters** | 19,129 | **−93.5%** |

Per tool, same-calls scenario: `get_transactions` 149,559 → 38,796 (−74%); `list_agents`
100,880 → 8,048 (−92%). `list_accounts` is unchanged in that scenario (44,736 → 44,832) —
**the filters only pay when used**, which is why the tool descriptions now steer toward them
and why the filtered scenario is reported separately rather than blended in.

Age decay, measured on what `_prepare_messages` actually emits: **25.1%** of the sent
transcript at steady state for a non-shrinkable tool, **0%** for sessions at/below the median
length (by design), and **0%** — no regression — for a repeatedly-called shrinkable tool.

## Consequences

- `get_transactions`' serialized schema grows ~975 chars (2,596 → 3,571) in the always-on
  core offer, paying for `account_ids`, `q_field`, `summary_only` and the `anyOf` forms. Net
  strongly positive against ~11K chars saved per call plus the N-calls-to-1 collapse, but it
  is a real recurring cost and is recorded here rather than buried.
- `get_signals` now returns `{signals, showing, total, truncated}` instead of a bare list —
  a shape change for that tool's consumers (AI only; the REST route is untouched).
- `list_agents`/`list_skills` roster rows omit the prompt/instruction body; a new
  `agent_id`/`skill_id` argument returns the single record in full. `allowed_tools` and
  `effective_tools` are deliberately KEPT in full, because `list_agents`' own description
  instructs the model to read them before calling an agent safe to enable — eliding a safety
  affordance to save a few hundred bytes is the failure this change exists to avoid.

## Not done (reported for pickup) — CLOSED by the 2026-07-19 follow-up below

17 of 19 tools accepting a `limit` still have no cursor/offset; only `get_transactions` and
`get_statement` can page. This lane fixed the ones it touched (`get_lots`,
`get_agent_findings` gained `offset` + `total`; `get_signals` gained `total`, and documents
why an offset is wrong for a live newest-first feed). Still outstanding: `fetch_emails`,
`get_agent_runs`, `get_category_suggestions`, `get_harness_suggestions`, `get_payees`
(reports both numbers already, no cursor), `get_self_improvements`,
`get_uncategorized_summary`, `list_checkpoints`, `list_match_decisions`,
`set_budget_rollover`, `test_rule`. `find_tools`/`web_search`/`x_search` were explicitly left
alone — different semantics.

## Follow-up (2026-07-19b) — closing the cursor gap, owner-directed

Owner instruction, verbatim: *"if we cap / we must ensure that tool support retrieving the
next one / capping and not allowing to move cursor,... is fucking stupid."* This follow-up
re-swept the WHOLE registry (274 tools) rather than trusting the "still outstanding" list
above — one entry on it turned out to be a false positive, and the sweep found no additional
qualifying tool beyond it.

**The sweep.** Grepped every `@tool` file for a `"limit"` schema property, for hardcoded
slice caps (`[:N]`), and for "most recent"/"top N"/"up to N" phrasing that would signal an
undisclosed cap with no schema knob at all. Each hit was checked against the actual
qualifying condition — *"it truncates its result set and the caller has no way to reach the
remainder"* — not just "does it have a `limit` parameter."

**Fixed (gained `offset` + honest `showing`/`total`, with a truncation note naming the next
step):**
- `get_category_suggestions` (`services/categorize.py` gained `_all_category_suggestions` +
  `count_category_suggestions`, mirroring the `get_agent_findings`/`AgentFindingStore`
  filter-then-count pattern) — the per-payee confidence ranking makes offset paging
  meaningful and stable.
- `get_harness_suggestions` (`db/harness_suggestion_store.py` gained `offset` + `count`,
  `services/self_harness.py` gained `count_suggestions`) — same shape; `status` stays the
  primary filter, offset reaches the rest within a status.
- `list_match_decisions` (`db/match_decision_store.py`'s `recent()` gained `offset`,
  `services/match_review.list_decisions` threads it through) — the verdict cache has no
  natural ceiling (it grows with the ledger's whole life), so offset is the only way to
  reach an old decision; `total` was already being computed but not exposed as a page
  disclosure — now it is (`showing` + a note).
- `get_self_improvements` (`services/agent_reflection.py` gained `_all_self_improvements` +
  `count_self_improvements`, with an offset param on `list_self_improvements`) — **also
  fixed a latent undercounting bug found along the way**: the old fetch used a guessed
  `limit*4` heuristic to compensate for self-improvements being a sparse subset of a finding's
  `proposed_change.type`; a payee/agent with self-improvements rarer than 1-in-4 among its
  findings could silently under-report the total. Now fetches the exact count via
  `finding_store.count()` first — bounded anyway (`MAX_FINDINGS=200`, global), so no new
  unbounded read.

**Fixed (report honest `showing`/`total` + a note, but via the "raise limit to the true
ceiling" next step, NOT offset — the store itself never retains more than the ceiling, so
there is nothing an offset could reach beyond it):**
- `get_agent_runs` — `RunHistoryStore` caps at 50 runs per entity (append-time trim); the
  tool now always fetches at that ceiling internally, trims to the caller's `limit` for
  display, and reports the true total.
- `list_checkpoints` — `ai/checkpoint.py` caps at `MAX_CHECKPOINTS=50` active (oldest
  pruned); gained `count_checkpoints()` (shares the new `_filtered_checkpoints` pass with
  `list_checkpoints` so total can never disagree with the page).

**Fixed (sample/aggregate distinction made explicit, no offset — the aggregate is already
exact and independent of the sample):**
- `test_rule` — `would_match`/`would_change` were ALREADY computed over the full matching
  set (never the capped sample), but the response didn't say so or disclose the sample's own
  truncation. Added `showing` + a note that names `would_change` as the authoritative total
  and points at `get_transactions` with the same match criteria as the way to browse every
  matching row, rather than raising `limit` on a tool whose job is a dry-run preview, not
  enumeration.

**Found already compliant — left alone, with why:**
- `fetch_emails` — already has a real continuation mechanism: the incremental mode's
  per-consumer UID cursor auto-advances and reports `more`, and the explicit
  `since`/`before`/`from_uid` params are themselves a cursor for backfill/recovery. This
  satisfies "the caller can reach the remainder" even though the field isn't literally named
  `total`/`showing` — a rename here would touch the RFC'd date-range/from-uid contract
  (`docs/rfc/email-date-scan.md`) for cosmetic parity only, so left as-is.
- `get_payees` — already ships `query` (name/alias substring, the FILTER-FIRST answer), an
  UNCAPPED `limit` (a caller can request the true total in one call), and a truncation note.
  This is the filter-first rung of the ladder, done correctly; an offset would be a strictly
  worse answer for "find a specific payee" than the filter it already has.
- `get_uncategorized_summary` — out of this lane's file scope (`ai/tools/ledger.py` +
  `services/ledger.py`, owned by the ledger-confirm-gate lane) but inspected anyway: `count`/
  `total_base` are computed over the FULL filtered set (never the peek), and `recent` is
  explicitly documented as "a small recent peek" (default 5, settable to 0). Already matches
  the `get_lots` "exact totals + labeled small sample" shape.
- `get_recent_logs` — a log TAIL, not a stored dataset with a natural whole; already has
  `level`/`q` filters (the filter-first answer) and is honest that `lines`/`total` describe
  what was returned, not a claim about the full log file. Paging a tail is "ask for more
  lines," which the tool already supports up to its own cap (500).
- `simulate_cashflow`, `auto_assign`'s preview `steps`, `import_transactions`' PDF/CSV
  preview `rows`, `diff_statement`'s `largest_legs`, and `get_screen_detail`'s transaction
  hydrator — all already pair a capped/sample list with an EXACT aggregate computed over the
  full set (`income_runway_months`, `total_delta`/`steps_count`, `rows_truncated`,
  `total_in`/`total_out`/`delta`, or exist purely as a screen-context snapshot with
  `get_transactions` as the real paging tool) — the `get_lots` pattern the original ADR
  already established, just not called out per-tool there.
- `list_agents`/`list_skills`/`list_rules`/`list_scheduled_tasks`/`list_grants`/
  `list_watchlists`/`get_alerts`/`get_uploads`/envelope & commitment listings — return the
  FULL collection with no `limit` at all; not a truncation, just a small natural domain
  (dozens, not thousands, of rows for a single-user finance app).

**Corrected, not fixed:** `set_budget_rollover` was on the original "still outstanding" list
but is not a bounded read at all — it is a single-category WRITE (`{limit, rollover}`) with
no list in its response. Removed from the punch list as a mis-classification, not a fix.

**The money rule held throughout:** every fix above adds row selection (`offset`) or an
exact aggregate — none adds a caller-chosen field projection. `_all_category_suggestions`/
`_all_self_improvements`/`_filtered_checkpoints` all `return dict(...)`-equivalent full rows;
nothing here lets a caller choose which fields of a money-bearing row come back.

**Schema/golden-file note:** `get_agent_runs`, `get_self_improvements`, `list_checkpoints`,
`list_match_decisions`, `get_harness_suggestions`, and `get_category_suggestions` all changed
their literal `input_schema` (a new `offset` property, or a reworded `limit` description) —
`tests/golden/tool_input_schemas.json` was hand-edited for exactly those six entries (deep
key-sorted to match the file's existing convention), per `test_schema_from_model.py`'s
"editing it by hand IS the review moment" contract. None of the six are on the derived-schema
migration list, so `assert_derived_matches_literal` doesn't apply to them.
