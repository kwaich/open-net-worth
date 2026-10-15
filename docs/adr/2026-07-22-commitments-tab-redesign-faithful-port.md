# Commitments tab redesign — faithful port of the design handoff

- **Date:** 2026-07-22
- **Status:** Accepted

## Context

`frontend/design_handoffs/commitments_handoff/` ships a complete interactive prototype
(`prototype/commitments_v3/`) for a redesigned Commitments tab: a funding-outlook masthead
(verdict + cash-cushion chart + Lucius ribbon), a consolidated "needs your attention" triage
lane, a filterable/sortable commitment list, a 4-tab detail screen (Overview/Schedule/Funding/
Actions) with full inline editing, per-installment funding-waterfall breakdown, loan
amortization intelligence, a "not happening" impact-analysis flow, and an interactive Lucius
side panel for closing funding gaps and drafting new commitments. Precedent: the Accounts and
Plan tab redesigns (`docs/adr/2026-06-28-...`, `docs/adr/2026-07-01-...`) — same bar, "pixel-
identical" per `port-design-handoff` skill.

The existing `frontend/src/components/tabs/Commitments.tsx` (632 lines) is a pre-redesign,
table-based implementation. The existing backend (`backend/services/commitments.py`, 551
lines) is **considerably more mature than the handoff's own prototype**: a real `coverage()`
funding-projection engine already runs a cash-only monthly projection (via
`services/cashflow.build_projection`, itself already floor-aware via
`profile.cashflow_low_balance_threshold`), a `detect_unmarked_payments` matcher for "looks
paid" suggestions, an `amortization_schedule` for loans, and `sync_commitment_envelopes` for
the Commitment↔Budget-target bridge. The handoff's prototype is a **client-only, in-memory,
zero-backend toy** (its own "Components & backend" page says as much: "production needs
persistence and a server-side coverage engine"). Porting it here means adapting the real, richer
backend data into the prototype's exact visual/interaction spec — not the reverse.

## Decision

### 1. Verbatim visual/interaction port, real-data seam

Port the prototype's DOM structure, CSS class vocabulary (`c2-*`/`c3-*`), and derivation logic
(coverage verdict, funding waterfall, loan intelligence, timeline strip, "not happening" impact
copy) into typed React/TS, seeded from the real `/api/commitments/*` endpoints instead of the
prototype's hardcoded `COMMITMENTS` fixture. `commitments2.css` + `commitments3.css` are copied
into `frontend/src/components/tabs/commitments/commitments.css`, stripped of shell scaffolding
(`.onw-nav`, `html/body`, the `c2-lux` squeeze-test panel, duplicate `:root` tokens — the app's
`index.css` already defines the same token set) and registered in
`frontend/scripts/check-css-classes.mjs`'s `CSS_FILES`.

### 2. The coverage/proposal engine is CLIENT-SIDE, over real backend data — not a new server engine

The handoff's own "Components & backend" page frames the server coverage engine as an
aspiration ("should be a server endpoint... the prototype's pure functions are a faithful
spec"), not a hard requirement. Building a full second server-side projection+proposal engine
in this session — duplicating the one that already exists in `services/commitments.coverage()`
— would fork the source of truth for "am I covered" between the REST list endpoint (which
already stamps `coverage` per row) and a new bespoke chart endpoint. Instead:

- `services/commitments.coverage()` is EXTENDED (additive, backward compatible) to return a
  `timeline: [{year_month, balance, total_in, total_out, is_tight}]` array (the monthly cushion
  series `build_projection` already computes internally but never returned) and to accept an
  optional `floor` override (defaults to the same `profile.cashflow_low_balance_threshold` it
  already reads) — this is what feeds the cushion chart.
- Per-commitment `at_risk` becomes FLOOR-aware (`balance < floor`, not `balance < 0`) to match
  the design's explicit floor semantics ("Risk = cushion dips below the floor, not this bill is
  unaffordable"). This is an intentional behavior change from the prior "only negative balance
  is risk" reading, updated with its own test coverage.
- The per-installment funding WATERFALL (`installmentFunding` — which source pays for which
  installment) and the fix-PROPOSAL generator (move/topup/defer, each re-simulated for its
  outcome) are ported as pure TypeScript functions, exactly mirroring `data3.jsx`/`coverage.jsx`,
  operating on the real `Commitment[]` + the extended `coverage()` response fetched over REST.
  Precedent: CLAUDE.md explicitly blesses this shape for `loanIntel` ("pure math, can stay
  client-side"); the same reasoning extends to the waterfall/proposal math, which reads already-
  fetched data and mutates nothing until a real write API call is made.

### 3. Lucius panel: real assistant, not a second scripted chat surface

The handoff's Lucius panel is explicitly, deliberately scripted (its own "Prototype scope"
callout: "the conversation is scripted, not model-driven... in production this becomes a real
assistant turn"). Building that real assistant turn — a new `commitments` `ProposedChange` type
threaded through `ai/tools/commitments.py`, prompt intent routing, and SSE rendering — is a
multi-layer AI-surface project on its own, and it would fight the grain of the **2026-07-20
write-then-verify ADR**, which retired confirm/dismiss proposal cards for ordinary writes in
favor of committing immediately and showing a differential chip (`GATED_TOOL_NAMES` is now
empty; the owner's own words: "please stop having these undo! just show!"). Re-introducing a
propose→confirm→apply loop for commitments would be building the exact pattern the rest of the
app just moved away from.

Instead, every "Fix with Lucius" / "Ask Lucius" / "Add with Lucius" / "New commitment" affordance
opens the app's REAL Lucius dock via the existing `onAskLucius(text: string)` prefill path (the
same mechanism the pre-redesign `Commitments.tsx` already used for its one "+ Add a commitment
with Lucius" button) with a rich, computed prompt — e.g. "The Bank X FlexiLoan draw is dated Sep
15 2026, about 11 weeks after the Feb 2027 installment it should cover. Pull it forward to Sep 1
2026 to close the S$X gap without new borrowing." The proposal text is genuinely COMPUTED by the
client engine (§2), not authored copy — only the "confirm" step routes through the real
assistant + real tools + real write-then-verify chip instead of a fake local Confirm button.
This is smaller, avoids maintaining two chat surfaces, and is the architecturally correct
direction per the app's own current conventions.

The "not happening" flow (marking an action as not going to happen, disabling the funding
source it unlocks, showing the plain-language consequence) is real, not chat-mediated: it is a
genuine write (new `missed` action status, `disabled` flag on the funding source) through the
existing PATCH/PUT endpoints, with the impact copy computed client-side exactly as the
prototype's `actionRole`/impact-line logic specifies, and a "Fix with Lucius →" button that
opens the real dock prefilled with the recovery context.

### 4. Backend schema additions (additive, non-breaking)

- `FundingSource.disabled: bool = False` — a source dropped by "not happening"; excluded from
  `projected_events`/`earmark_reconciliation`/the coverage timeline.
- `FundingSource.cadence: str | None` — informational ("recurring monthly" display); does not
  change existing income-as-baseline projection semantics.
- `CommitmentAction.funding_id: str | None` — ties an action to the funding source it unlocks
  (load-bearing for "not happening" impact analysis and the "unlocks funding" badge).
- `ACTION_STATUSES` gains `"missed"` alongside `pending`/`done` — persisted, reversible (PATCH
  back to `pending` + re-enable the funding source), not a parallel client-only list like the
  prototype's `missedActions` array (the prototype has no backend to persist to; we do).
- `Commitment.loan: {principal, apr, term_months, payment} | None` — stored at creation time by
  `create_loan_repayment` (previously only used transiently to generate installments, then
  discarded) so the detail screen's loan-balance stat + early-payoff insight (`loanIntel`, pure
  client math per §2) has its inputs.

No existing field is renamed or removed; every addition is optional/defaulted so a pre-existing
`commitments.json` row keeps working unmigrated (`_make_funding_source`/`_make_action` supply
the default on read, matching the store's existing normalization convention).

### 5. What's deliberately deferred (see `PARITY_AND_GAPS.md`)

Real model-driven Lucius proposal cards (a genuine `ProposedChange` type, replacing the
prefilled-prompt handoff), a live-synced "Synced target" panel reading `per_period`/`accumulated`
directly off the linked Planning envelope's current month (v1 reads it best-effort via the
existing `/api/planning/budget-month` envelope row, not a new dedicated join), and per-
installment reminders (notification scheduling) are out of scope for this pass.

## Consequences

- One coverage engine stays authoritative server-side; the client only adds pure, re-derivable
  waterfall/proposal math over data the server already returned — no drift between the list
  view's `coverage` stamp and the masthead's chart.
- The feature ships fully functional (every write is real, nothing is a scripted illusion) while
  reusing the app's actual chat/assistant machinery instead of forking it.
- Floor-aware `at_risk` is a genuine behavior change from today's negative-balance-only reading;
  flagged here and covered by updated backend tests rather than silently shipped.

## Agent provenance

- **Decided by:** the orchestrating Claude agent (mission-control task 80, autonomous run — no
  interactive owner available mid-flight).
- **Lane / branch:** `mc/80-reimplement-the-commitment-flow-feature`.
- **Alternatives weighed:** a second client-only coverage/projection engine mirroring the
  prototype exactly (rejected — the real backend's engine is more correct, already includes the
  income/expense baseline, forking it would be a downgrade); a real model-driven Lucius
  `ProposedChange` type for commitments, full assistant-turn integration (rejected for THIS pass
  — a multi-layer AI-surface project on its own, and it would reintroduce the propose→confirm
  pattern the 2026-07-20 write-then-verify ADR just retired); building the design's scripted
  local chat panel verbatim as a stand-in (rejected — a second, parallel, fake chat surface is
  worse than routing through the real one, even prefilled rather than fully AI-authored).
- **Driving artifacts:** `frontend/design_handoffs/commitments_handoff/` (prototype +
  `05-components.html`'s own "Action needed" backend-surface callout); `docs/adr/
  2026-07-20-write-then-verify-chip.md`; the existing `backend/services/commitments.py`
  funding-projection engine (found substantially more mature than the handoff assumed).
- **Why this way (one line):** port the visual/interaction design faithfully and make every
  control genuinely work against real data, without forking the app's ONE coverage engine or
  building a second chat surface to fake a feature the real assistant can already do better.
