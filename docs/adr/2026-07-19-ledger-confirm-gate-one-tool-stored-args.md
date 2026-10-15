---
title: The ledger confirm gate — one tool, two calls, stored args (not propose/apply, not dispatch interception)
status: Accepted
date: 2026-07-19
deciders: reliability-program agent (Claude) + repo owner (harness reliability sweep)
---

# The ledger confirm gate — one tool, two calls, stored args

## Context

Transcript forensics over 289 real sessions / 3,507 tool calls found the confirm-protection
priority **exactly inverted**:

- **10.5% of all write turns "succeeded and were still wrong"** — 80 of 763 write-bearing
  assistant turns are followed by a short user turn and a re-issue of the SAME write tool
  against the SAME entity id. **In most of them there is no error tool_result at all**: the
  write committed cleanly and was wrong.
- **`update_transaction`: 39 redos in 231 calls — 17% of every edit is a redo of a rejected
  edit.** `add_transactions`: 16. The looser signal (same write tool re-issued) is 19% of
  write turns.
- Meanwhile the existing propose/apply protocol guards `lot_dispose`, `position_add` and
  `vest` — tools called **13–16 times in the entire corpus** — while `update_transaction`,
  the #4 tool, had no preview tier at all.

The structural cause is specific and fixable: **category precedence (explicit > rule >
payee-default > None) and the spend-rules engine resolve SILENTLY inside
`create_transaction`.** The field the user actually wanted is only discoverable after the
commit. A live incident (2026-07-17) is the canonical shape — a GrabFood order was booked with
category "Daily Meals", an `override_existing` rule stored "Transport", and nothing surfaced
the substitution.

## Relationship to ADR 2026-07-18 (remove the layered AI permission system) — READ THIS FIRST

That ADR, owner-decided one day before this one, tore out the T0/T1/T2 tiers, the headless
ceiling, the per-turn scope narrowings, the auto-apply grants, and — most relevant here —
"**the propose→wait→apply protocol** *as a permission mechanism* ... What goes is the
enforcement that a write is unreachable until a separate `apply_*` call." Code review flagged
this change as a possible unacknowledged re-introduction of exactly that. The reconciliation
matters enough to state precisely, because it is the difference between honouring that
decision and quietly undoing it.

**What that ADR removed, this change does not add back.** There is no tier, no capability
bundle, no allowlist, no second `apply_*` tool, and no notion of a caller who is authenticated
but still constrained. One tool per write, as before. Nothing here asks *"may this caller do
this?"* — the question the tiers asked and the owner rejected.

**What that ADR explicitly KEPT is precisely this mechanism.** Its "What is explicitly KEPT"
section, item 2, preserves *"nonce staging that fires server-STORED args"* on the grounds that
it is **anti-tampering, not gating** — "the approval never carries the arguments back in ...
That makes an approve-then-substitute attack structurally impossible rather than merely
unlikely," and, decisively, "this is **the load-bearing part of a confirm flow** and the tier
ladder around it was not." `PendingPlanStore` is a second instance of exactly that mechanism,
built by copying `McpPendingCallStore`, the file that ADR names. The same section also keeps
"the confirm card itself".

**And its closing rationale asks for this change.** The owner's objection to the tiers was
that they "were guessed, not measured ... an a-priori guess at where harm might occur," and
that they defended against a threat that does not exist here, since "the worst outcome of an
unwanted write is a wrong number on a screen, not a lost dollar." This gate is the inverse on
both counts: it is derived from 289 measured sessions rather than guessed, and *a wrong number
on a screen is the exact harm it addresses* — 10.5% of write turns produced one, and the user
had to notice and re-issue the write. The ADR states the owner "would rather run the system
with real usage and design a real gate from observed failures." This is that gate.

**The one genuine judgment call, stated plainly for owner sign-off:** on the web surface a
gated write now takes two calls instead of one, so *in a literal sense* a write is briefly
"unreachable until a second call." That is a real behavioural interposition on a path that had
none yesterday, and it is a product decision, not a security one — nobody but the owner should
ratify it. It is deliberately scoped to be cheap to reverse: two tool names in
`GATED_TOOL_NAMES`, one surface, and deleting a string turns it off per tool. **No agent
approval substitutes for the owner's here.**

## Decision

### 1. The preview shows the RESOLVED row, produced by the write path's own resolver

`services/ledger.create_transaction`'s resolution body — leg normalisation, the
base-currency snapshot, split allocation, the rules engine, payee resolution and the
category-precedence fold — is extracted verbatim into
**`ledger.resolve_transaction_row(data, *, mint_payee, skip_payee_resolve) -> (row, meta)`**.
`create_transaction` calls it and then appends; `services/ledger_preview` calls it and
renders. **One implementation, two callers** — a parallel preview implementation would drift
from the writer, and drift is precisely the bug class being fixed.

Extraction was chosen over a `dry_run=` flag on the writer: the extracted function *contains
no store write at all*, so a preview cannot commit by taking a wrong branch. A flag inside the
writer is one missed early-return away from writing.

`meta` carries what only the resolver can know, notably **`category_source`** — which
precedence tier actually decided the stored category (`explicit` | `rule` | `payee_default` |
`none` | `split`). The card names the tier rather than re-deriving it.

### 2. Previews stay pure — `mint_payee=False`

The one entity-creating step in the resolution path is payee resolution's last-resort
`resolve_payee_id(create=True)` mint. Under `mint_payee=False` it is suppressed and reported
as `meta["payee_is_new"]` instead. This is the same **"resolve read-only, report the intent"**
shape ADR `2026-07-18-previews-must-not-write` established for `propose_position_update`'s
not-yet-existing account, and it is applied for the same reason: a rejected or mistyped
proposal must leave nothing behind.

The category outcome is provably unaffected — a freshly minted payee has no default category
by construction, so the blank-category fold lands on `None` (Unsorted) either way.

### 3. One tool per write, gated on a stored-args token — NOT propose/apply

**Rejected: a `propose_transaction` / `apply_transaction` pair.** It doubles the tool surface
on the hottest path in the registry, and the two-tool protocol is already what the model gets
wrong.

**Rejected for this slice: dispatch interception in `assistant.py`** (Seam D of the W4 design
in `.claude/rfcs/harness-audit-2026-07-18/`). It is the better end state — no model
round-trip on confirm — but it makes the confirm endpoint a primary write path that runs
*outside* the turn, and the W4 design's own security requirement #1 is the reason to defer:
`inject_session_note` appends to shared history with no lock, and `_fix_dangling_tool_uses`
only inspects `history[-1]`, so a confirm that splices a user message between a `tool_use` and
its `tool_result` leaves an unhealable orphan at `history[-3]` and the session 400s
**permanently**. That hazard is not worth absorbing in the same change as the ledger preview.

**Chosen: the same tool, called twice.**

1. **No token** → validate, build a pure preview, stage the *validated args* under a
   single-use token in `db/pending_plan_store.py`, emit a `proposed_change`. The dispatch
   loop's existing `emitted_proposed` terminator ends the turn, so nothing else runs.
2. **With token** → `pop` the stored args and execute **those**. Every other argument on the
   confirming call is discarded.

Step 2 is the security property and is what makes a one-tool split as strong as a two-tool
one: **a card showing "$12.40 Groceries" cannot commit "$1,240 Rent"** regardless of what the
model reconstructs from conversation history or what a prompt injection substitutes. This is
deliberately the fourth instance of a reviewed, already-shipped mechanism — external MCP
(`McpPendingCallStore`), Alpaca (`AlpacaPendingOrderStore`) and `run_code` all bind their
confirm to stored args through this exact nonce pattern, and `ChangePreview` already has
nonce branches — not a new mechanism.

**The token is deliberately absent from the tool result** — it rides only the `proposed_change`
card. The only way it enters the model's context is the user tapping ✓, which makes the
frontend echo it back. So the gate holds on *structure* rather than on the model complying
with a "wait for the user" instruction: a model that ignores the instruction has nothing to
redeem. (This is a tightening over the three existing nonce flows, which do surface their
nonce to the model.)

The accepted cost is **one model round-trip per confirmed write**. The endpoint-based confirm
(W4 step 4c) is a pure addition on top of the same store when it is taken up.

### 4. One card for N rows

A bulk add previews as a single `txn_bulk` card: a capped 12-row resolved sample, the true
`count`, totals over **all** rows, and the rules that fired with hit counts. Fanning a bulk
write into N confirmations would just move the round-trips from the API to the human.

### 5. Gated on a CARD-CAPABLE surface — a positive allowlist of one

The first implementation gated on "is this an interactive turn" (`_is_interactive_scope`).
**Security review proved that wrong**, and the distinction is the most important lesson in
this change: an inbound Telegram/Slack turn is deliberately *not* marked unattended (a real
human is waiting), so the interactive test passes — but that surface renders a proposed
change through `format_change_summary`, which reads a `display` key our change does not have,
and applies it through `chat/confirm.apply_change`, which has no branch for our types and
falls through to the positions choke. Gating it **broke booking a transaction over chat
entirely**. Inbound MCP and the REST finding-apply were also caught staging and reporting
`ok: true`, which `api/agents.py` reads as applied.

So the gate asks the narrower, honest question — *can this surface show the user the card and
apply it?* — and answers it from `core.cost_context.get_cost_surface()`, the existing
per-turn seam every entrypoint already sets (`web` / `connector:<name>` / `scheduled` /
unset). Only `web` gates. Every other surface, present or future, defaults to today's ungated
behavior, which is the fail-safe direction: an ungated write is the status quo; a staged write
nobody can confirm is a broken workflow.

Two further narrowings: `is_unattended()` (belt and braces), and an active **skill** scope — a
`run_skill` playbook is a scripted sequence that may batch many writes, and a card per write
would break the script. An `@agent` responder turn sets the *offer* scope rather than the
active scope, so it still gates, matching the documented responder contract that its writes
confirm through the normal flow.

The predicate also **fails open** on any error, for the same reason.

`GATED_TOOL_NAMES` is both the flag and the policy, so rollback per tool is deleting a string.

### 6. The token binds the OUTCOME, not just the args

Also from security review: staging the args alone is not enough, because redemption re-runs
the rules engine and the payee defaults, and `create_rule` / `set_payee_default_category` are
ungated tools reachable **in the same turn as the confirm**. A model — or a prompt injection
already sitting in context from a fetched page or an email body — could therefore change what
the staged args resolve to *between* the card and the ✓, and the row would commit under a
category the user never saw, carrying their approval. Since the resolved category is precisely
the field this feature exists to surface, it is the field the token must bind.

The staged plan therefore also records the outcome the card promised (resolved category, base
amount, row count). On confirm the handler re-resolves and compares; a mismatch **refuses and
writes nothing**, telling the model to re-preview. The row commits as shown, or not at all.

Including `base_amount` means a foreign-currency draft could in principle refuse if the FX
cache moved mid-window. In practice that needs the background refresh job to run *and* the
rate to shift enough to move a 2-dp figure inside 15 minutes (`load_fx_cache` is a passive
file read, not a fetch) — and when it does happen, refusing is the correct answer: the base
amount is a money figure printed on the card, and silently committing a different one is the
class of bug this whole change exists to stop.

## Consequences

- `add_transactions` and `update_transaction` preview before writing on interactive turns.
  Deletes are NOT gated in this slice — the redo evidence is on add + edit.
- REST, imports, connectors and every unattended path are unchanged; the gate is on the AI
  tool surface only, and writes still go through `create_transaction` /
  `bulk_create_transactions`. **No second write path exists.**
- `pending_plans.json` lives in `CACHE_DIR`, chmod 600, capped at 200, 15-minute TTL, and is
  **checkpoint-EXCLUDED** (written inside an interactive turn's CheckpointContext: stage
  persists, confirm pops — the `MCP_PENDING_CALLS_JSON` rationale exactly).
- `add_transactions`'s description was recompressed from 1,762 → 1,282 chars **while gaining**
  the preview clause, restoring the 1,500-char per-tool budget the import lane had broken. The
  cuts were restatements and enforcement prose that the runtime errors already carry.
- The outcome binding in §6 is a narrow, targeted form of the W4 design's `state_digest`. It
  covers the fields the card leads with (category, base amount, count). A fuller digest over
  every entity the preview read remains the natural next increment on this store.
- **Known limitation (deliberate):** for a multi-row add the duplicate pre-flight runs on the
  *confirming* call, so an `already_recorded` abort surfaces after the ✓ rather than on the
  card. The batch is still never written.
- **Known cost (deliberate):** a gated tool keeps its `mutates=("ledger",)` tag — it *is* a
  writer — so a preview turn still emits one `data_changed {ledger}` SSE and the frontend
  refetches a ledger that did not change. Harmless (an idempotent GET) and not worth teaching
  the dispatch loop a per-call "did it actually write" signal, which would be a fragile
  coupling for one wasted request.
- **Known gap (deliberate, and it weakens the guarantee — say so out loud):**
  `bulk_update_transactions` takes `transaction_ids` + `category` and writes immediately
  through the same choke with no gate. A model asked to fix one miscategorized row can call
  it with a single id and land exactly the silent-resolution write this feature exists to
  make visible. So the gate raises the floor; it does not close the path. It is left ungated
  here because the measured redo evidence is on `add_transactions` / `update_transaction`, and
  because widening the gate late in this change trades a known-good slice for an unmeasured
  one. Gating it (or routing single-row edits to `update_transaction`) is the obvious next
  increment, and should be decided on the same evidence footing rather than by reflex.
- `update_transaction` now reports `ok:false` with a prescriptive message when the target row
  no longer exists, instead of the prior `ok:true, transaction:null`. Pre-existing, but the
  gate widens the window in which the row can vanish between preview and ✓, so it is fixed
  here rather than left to bite the new flow.

## Agent provenance

The `_add_one` single-row path stages the caller's own `input_data`, which is itself a valid
bare `add_transactions` payload — so redeeming a token replays the identical call through the
normal path rather than needing a separate execution branch. Verified during implementation
that `apply_rules` is genuinely pure ("PURE — no writes here (the caller persists)"), that
`resolve_payee_id(create=False)` is pure, and that `_apply_splits` / `_validate_legs` are
validation-only — so the extracted resolver's only write was the payee mint, which is the one
thing `mint_payee=False` suppresses.

A defect found and fixed by the tests rather than by review: the preview initially read
accounts and payees through freshly constructed `AccountStore()` / `PayeeStore()` instances
instead of the ledger service's own. In production the paths coincide, so it would have
shipped silently; it is corrected to read through `ledger.accounts` and
`payee_registry.require_payee` so **the preview and the write always read the same book**.
