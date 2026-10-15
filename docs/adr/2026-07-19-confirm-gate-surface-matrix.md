---
number: 2026-07-19-confirm-gate-surface-matrix
title: Confirm-gate surface matrix — connectors get the ledger confirm gate, headless gets a pre-authorized envelope
status: Accepted
date: 2026-07-19
deciders: senior-engineer lane (lane/txn-confirm-gate-surfaces) + repo owner (via coordinator correction mid-lane)
---

# 2026-07-19. Confirm-gate surface matrix — connectors get the ledger confirm gate, headless gets a pre-authorized envelope

## Context

The ledger confirm gate (`docs/adr/2026-07-19-ledger-confirm-gate-one-tool-stored-args.md`)
keys on `ai.tools._confirm_gate._CARD_CAPABLE_SURFACE = "web"`. That was deliberate and
correct at ship time: a chat-connector turn (Telegram/Slack/Discord/WhatsApp) renders via
`connectors.processor.format_change_summary` (which reads `change["display"]` — empty for a
ledger card, since `services.ledger_preview` returns a FLAT shape) and applies via
`connectors.chat.confirm.apply_change`, which had **no branch** for `txn_create` /
`txn_update` / `txn_bulk` and fell through to the positions choke point. Gating those
surfaces would have staged a plan nobody could ever confirm — a stuck write, worse than an
ungated one. Meanwhile a scheduled task / agent run (`main._scheduled_agent_runner`, surface
`"scheduled"`) was never gated at all: `email-transaction-logger` books transactions from
email — attacker-influenced content — completely unattended, with nobody watching.

That is inverted: the surface where a human is *guaranteed present* (web) was the only one
gated; the surface where *nobody* is present had no safeguard beyond the interactive gate's
own fail-open default.

Two designs were considered for the headless case over the course of this lane, in sequence:

1. **"Headless never blocks — write, then surface to the Inbox."** The initial brief. Simple,
   and consistent with "an ungated write is the status quo."
2. **"Pre-authorized envelope, fail closed outside it."** A correction issued mid-lane after
   research located two real agent-harness precedents that converge independently on this:
   OpenClaw's `askFallback` (docs.openclaw.ai/tools/exec-approvals — the resolution when a
   prompt is required but no UI is reachable) **defaults to `deny`**; Hermes's `cron_mode`
   (hermes-agent.nousresearch.com/docs/user-guide/security) governs the same situation for
   scheduled jobs and also **defaults to `deny`** (Hermes's approval model was migrated from
   OpenClaw's, so this is one lineage, not two independent designs — still the only two
   *comparable production precedents* surfaced). OpenHands's headless "always-approve, cannot
   be changed" mode is the counter-example, safe only because it runs in a disposable sandbox
   — a ledger is not disposable.

Design 2 superseded design 1 for this lane. This ADR records the final surface matrix and the
boundary between what this lane implemented and what remains for the lane(s) owning
`ai/tools/*` (this lane was explicitly scoped to `backend/connectors/`,
`backend/services/chat/`, `backend/api/chat.py`, `backend/ai/_confirm_gate.py` — NOT
`ai/tools/*`, owned by seven other concurrent lanes in this harness run).

## Decision

We will treat the confirm gate as a **three-surface matrix**, not a single web/not-web split:

| | routine write | model-chose-the-set write | outside-envelope (headless only) |
|---|---|---|---|
| **web** | write + chip | confirm card | n/a |
| **connector** | write + chip | card (Telegram/Slack/Discord native buttons; WhatsApp Cloud-API interactive buttons with its own graceful text fallback) | n/a |
| **headless** (scheduled task / agent run) | write + notify, INSIDE a pre-authorized envelope | **never runnable** — no envelope, no config, no override | **refuse + signal**, never blocks (the refusal itself doesn't need a human to complete) |

"Model chose the set" = an operation whose selection can't be verified after it runs
(`delete_transactions_where`'s filter, a global `apply_payee_rules` rescan — "this matched 847
rows" is information available no other way). We explicitly did **not** reintroduce gating on
new-entity creation, ambiguous resolution, or amount thresholds — those are routine and stay
write + chip/notify on every surface, per the owner's standing rejection of those triggers.

Concretely, in the files this lane owns:

1. **`backend/connectors/chat/confirm.py`** — `apply_change` gained a branch for
   `txn_create`/`txn_bulk` → `add_transactions`, `txn_update` → `update_transaction`. It
   redeems the SAME server-stored `confirm_token` the web path uses via
   `ai.tools.execute_tool`, never reconstructing args from the connector's own `change` dict —
   the property that makes a card showing $12.40 unable to commit $1,240 survives this wiring
   unchanged. The tail refusal check was widened from `result.get("error")` to also catch
   `result.get("ok") is False` without an `"error"` key (an `already_recorded` /
   `suspected_duplicate` abort uses `"error_note"`/`"message"` instead), mirroring the
   convention `_apply_agent_finding` in the same file already used — otherwise a connector tap
   that hit a duplicate-guard abort would have been reported "applied" while nothing wrote.
2. **`backend/connectors/processor.py`** — `format_change_summary` gained a FLAT-shape-aware
   branch (`_format_txn_summary`) for the three ledger types, showing the resolved category,
   account and amount — the same information the web `ChangePreview` card shows.
3. **`backend/ai/_confirm_gate.py`** (new file, deliberately NOT under `ai/tools/`) — the
   shared POLICY primitives every caller needs: `can_render_card(surface)` (web + any
   `connector*` surface = True; `scheduled`/unset = False — item-4 prep, see Follow-ups),
   `HARDLINE_UNATTENDED_TOOLS`/`is_hardline_blocked` (the never-runnable-unattended set),
   `Envelope`/`evaluate_envelope` (the pure allow/refuse math for an unattended write against
   a per-agent account/kind/category/amount boundary — `envelope=None` fails closed), and
   `surface_unattended_write`/`surface_unattended_refusal` (best-effort Inbox signal +
   `notify()`, never raising).

## Consequences

### Positive

- A connector button tap on a ledger confirm card now actually books/updates the transaction
  through the real stored-args redemption, instead of silently falling through to the
  positions choke (which would either no-op or misapply the change).
- The headless design now matches two real, independently-converged precedents instead of an
  unqualified "write and hope someone notices" — an agent that would touch an account/
  category/amount outside its declared boundary is refused, not merely reported after the
  fact.
- The policy math (`Envelope`, the hardline set, the surface predicate) is isolated,
  dependency-light, and unit-testable without booting the tool-dispatch loop — any lane can
  wire it in without re-deriving the allow/refuse rules.

### Negative

- **The envelope is not yet enforced in production.** This lane could not add the field an
  agent's envelope would be declared in (`models/agent.py` / `db/agent_store.py`) or the call
  site that checks it before an unattended ledger write (`ai/tools/ledger.py` /
  `ai/tools/_confirm_gate.py`) — both are outside this lane's file scope (owned by concurrent
  lanes in this harness run; see Follow-ups). Until that wiring lands, `email-transaction-logger`
  and every other unattended caller remain exactly as ungated as before this change. This ADR
  records the decided design and ships the primitives; it does not yet close the gap the
  design fixes.
- **`can_render_card`/item 4 (flip `ai.tools._confirm_gate`'s surface predicate from
  `== "web"` to this predicate) is also unwired**, for the same file-ownership reason. Until
  it's flipped, connectors still never see a ledger `proposed_change` at all — items 1–2 above
  are "ready but inert" against production traffic, proven correct by tests that drive the
  real staging path directly rather than through a live connector turn.
- The widened `apply_change` refusal check (`ok is False`, not just an `"error"` key) is a
  behavior change for every OTHER change type `apply_change` dispatches (positions, lots,
  agents, budget, income, skills, lessons) — audited against every service function's return
  shape (none intentionally returns `{"ok": False}` on a non-error path); low risk, but it is a
  broadened check on a shared function, not a ledger-only change.

### Follow-ups

- Wire `ai.tools.ledger`'s unattended branch (`is_unattended()` true, `should_gate()` false) to
  call `ai._confirm_gate.is_hardline_blocked` / `evaluate_envelope` / `surface_unattended_*`
  before executing — owned by the lane(s) with `ai/tools/*` in scope.
- Add the envelope's persisted home — most likely a new optional field on `models.Agent`
  (`db/agent_store.py` + `models/agent.py`), given `allowed_tools` is the existing per-agent
  configuration surface the coordinator pointed at. Out of scope here (not
  `backend/connectors/`, `backend/services/chat/`, `backend/api/chat.py`, or
  `backend/ai/_confirm_gate.py`).
- Flip `ai.tools._confirm_gate.should_gate`'s `_CARD_CAPABLE_SURFACE` check to call
  `ai._confirm_gate.can_render_card(get_cost_surface())` instead of the hardcoded `== "web"`
  — item 4, contingent on 1–3 being solid (they are, per the Verification section below) but
  gated on that file's ownership.
- Re-scope `GATED_TOOL_NAMES` (currently `{add_transactions, update_transaction}`) toward
  "model chose the set" tools once `delete_transactions_where` gains its own preview/stage
  path — currently that tool has no `proposed_change` shape to stage at all, so gating it is
  new work, not a rename.

## Alternatives considered

- **Headless never blocks (the original brief)** — rejected mid-lane once the two-precedent
  research landed; "write and hope someone notices" has no real-harness precedent behind it,
  and OpenHands's actually-unsafe always-approve mode is explicitly a warning, not a model, in
  its own docs.
- **Gate every unattended write on interactive-style preview+confirm** — rejected: nobody is
  present to tap ✓, so this just breaks the automation (the original, still-correct reasoning
  for why `is_unattended()` fails the interactive gate open).
- **Extend `agents.json`'s `allowed_tools` itself to encode the envelope** (e.g., a tool-name
  convention like `add_transactions:acc_checking`) — rejected: conflates a TOOL offer with a
  DATA-SHAPE boundary; `allowed_tools` is a flat tool-name list with no room for structured
  constraints, and overloading its strings would make every other reader of that field (the
  lazy-tool-offer machinery, the catalog, the alias resolver) need to know about a second
  grammar living inside it. A dedicated field is more legible even though it wasn't built in
  this lane.
- **Gate on new-entity creation / ambiguous resolution / amount thresholds** — explicitly
  rejected per the owner's standing decision: a first-time merchant is a daily, low-harm event
  (a deletable row); an ambiguous account/category already RAISES or degrades safely at
  resolution time, so gating on top of that duplicates an existing safeguard; an amount
  threshold has no principled cutoff and was previously tried and rejected.

## Update (2026-07-19b — follow-up lane closed items 1 and 3)

A second lane (`lane/headless-confirm-gate-envelope`, scoped to `backend/ai/tools/_confirm_gate.py`,
`backend/ai/_confirm_gate.py`, `backend/models/agent.py`, `backend/services/agents.py`,
`backend/db/agent_store.py` — explicitly NOT `ai/tools/ledger.py` or the other tool-group
files owned by concurrent lanes in this harness run) closed two of the three Follow-ups:

- **Item 3 (the surface flip) is DONE.** `ai.tools._confirm_gate.should_gate` now calls
  `ai._confirm_gate.can_render_card(get_cost_surface())` instead of the hardcoded `== "web"`.
  A connector turn is now exactly as card-capable as web chat.
- **The envelope's persisted home is DONE.** `models.Agent.unattended_write_envelope`
  (JSON-shaped: lists, not frozensets — a list becomes a frozenset when
  `ai.tools._confirm_gate._envelope_from_config` converts it into `ai._confirm_gate.Envelope`
  right before evaluation); `db/agent_store.py` normalizes/stores it (leniently, mirroring
  every other field there); `services.agents.validate_agent` gives a bad shape a readable
  rejection at author time; `services.agents.resolve_unattended_envelope(agent)` is the single
  read-time resolver every caller uses.
- **The composed call is DONE and tested, but still NOT WIRED into `ai/tools/ledger.py`.**
  `ai.tools._confirm_gate.enforce_unattended_write`/`report_unattended_write` compose the
  hardline block, the envelope evaluation, and the best-effort surfacing into the ONE call a
  ledger write path should make before it writes. Proven directly by
  `tests/test_unattended_write_envelope.py` (a headless write inside its envelope proceeds
  and reports after the fact; a refusal outside it signals and never blocks/raises; the
  hardline block is unreachable regardless of envelope config) — the same "prove the
  primitives before their call site" approach this ADR's own Verification section already
  used. **Item 2 remains genuinely open**: until a lane with `ai/tools/ledger.py` in scope
  adds the one call, every unattended caller — `email-transaction-logger` included —
  continues to write completely ungated, exactly as before this whole effort. Nothing in this
  update changes that fact; it only removes every OTHER reason the wiring lane would have had
  to also touch `models/`, `db/`, or `services/agents.py` itself.

**Migration-path decision (the ADR's own Follow-ups didn't specify one; this lane had to
choose):** an agent authored before this field existed has no `unattended_write_envelope` key
at all. The two candidates offered were "default-permissive-with-a-warning" (treat absence as
unconstrained, but surface a nag) and "a seeded envelope." Both, read literally, are actually
the SAME shape once you ask "how would you even implement a warning-but-permissive default" —
you'd have to either (a) special-case "no envelope" to mean "allow everything," which is
exactly the "unconstrained for everyone" design this whole ADR's headless correction exists to
reject (it would silently re-open the gap for every FUTURE agent someone forgets to configure,
not just the one this is meant to protect), or (b) seed something. This lane chose (b), scoped
as NARROWLY as possible: a fresh install seeds `email-transaction-logger` with a REAL,
explicit, bounded envelope directly (`db/agent_store.py`'s `BUILTIN_AGENTS` entry — expense/
income only, matching its own job description's step 4b refusal to log a trade; any account;
any category; a $5,000 ceiling), so a brand-new install never depends on any fallback at all.
For an install seeded BEFORE this field existed (so `seed_builtins()`, which never overwrites
an existing row, left it with no key), `services.agents._LEGACY_ENVELOPE_DEFAULTS` is a
one-entry, explicit, reviewable table — the same "rollback is deleting a name from a set"
shape `HARDLINE_UNATTENDED_TOOLS` already uses — consulted ONLY for that one named id, and
ONLY when the stored row also carries `builtin: True` (belt-and-braces against a hypothetical
future id-squatting path; today neither the AI `create_agent` tool nor the REST `AgentCreate`
model accepts a caller-supplied id at all, so this is pure defense-in-depth, not a closed
hole). Every OTHER agent with no declared envelope — including every other built-in and every
user-authored agent — resolves to `None` and fails closed, once wired, exactly as the
original design intends.

## Agent provenance

- **Decided by:** senior-engineer lane (dispatched sub-agent), acting on a mid-task correction
  relayed from the repo owner via the orchestrating coordinator.
- **Lane / branch:** `lane/txn-confirm-gate-surfaces`, worktree
  `.claude/worktrees/agent-af125d4bea648ea35`, branched from `harness/reliability-program`.
- **Alternatives weighed:** see "Alternatives considered" above — the headless design changed
  once during this lane (write-and-surface → pre-authorized-envelope-fail-closed) on the
  strength of the OpenClaw/Hermes precedent, not by this agent's own initiative.
- **Driving artifacts:** `docs/adr/2026-07-19-ledger-confirm-gate-one-tool-stored-args.md` (the
  gate this one extends); `docs.openclaw.ai/tools/exec-approvals` and
  `hermes-agent.nousresearch.com/docs/user-guide/security` (the two precedents cited in the
  coordinator's correction); `docs/adr/2026-07-18-remove-layered-ai-permission-system.md` (why
  `allowed_tools` is an offer, not a ceiling, and why the hardline tier has to be a NEW,
  narrower restriction rather than a reversion of that ADR).
- **Why this way (one line):** the two real agent-harness precedents the owner named both
  default an unattended dangerous action to *deny*, not *allow-and-notify*, so the headless
  column of this matrix follows them rather than the lane's own initial (and, on reflection,
  weaker) design.

**Provenance, Update 2026-07-19b:**

- **Decided by:** senior-engineer lane (dispatched sub-agent), continuing this ADR's own
  Follow-ups list.
- **Lane / branch:** `lane/headless-confirm-gate-envelope`, worktree
  `.claude/worktrees/agent-a3c9b3a6f5fde8470`, branched from `harness/reliability-program`.
- **Alternatives weighed:** the migration-path choice above (default-permissive vs. a seeded
  envelope) — argued to converge to the same shape in the "Update" section's reasoning, since
  a genuinely permissive default would reopen the exact gap the parent design closes; settled
  on the narrowest possible seeded/grandfathered form instead.
- **Driving artifacts:** this ADR's own Follow-ups section (the three items closed/scoped
  above); `docs/adr/2026-07-18-remove-layered-ai-permission-system.md` (why `builtin`/`id`
  aren't currently attacker-reachable, which is what makes the belt-and-braces `builtin: True`
  check pure defense-in-depth rather than closing a live hole).
- **Why this way (one line):** "breaking the owner's running email-transaction-logger is a
  worse outcome than an ungated write" (the task's own non-negotiable) is satisfied by
  grandfathering exactly the one agent it names, not by weakening the fail-closed default for
  every agent that will ever exist.

## Update (2026-07-19c — item 2 CLOSED: the composed guard is now actually called)

A third lane (`lane/wire-unattended-write-guard`, scoped to `backend/ai/tools/ledger.py`,
`backend/ai/tools/_confirm_gate.py`, and their tests) closed the last open Follow-up: **the
composed guard is now wired into every real ledger write in `ai/tools/ledger.py`**, not just
proven in isolation.

- `_add_one`/`_update_one` (the single-row create/update paths) call
  `ai.tools._confirm_gate.enforce_unattended_write` after validation/preview and before the
  actual `svc.create_transaction`/`svc.update_transaction` call, and call
  `report_unattended_write` after a write that lands, guarded on `is_unattended()` (which
  `enforce_unattended_write` itself does not need, since it self-guards, but
  `report_unattended_write` has no self-guard of its own and must never fire on an ordinary
  interactive write).
- The TRUE multi-row batch paths — `add_transactions`' statement-import `to_create` write and
  `update_transactions`' N>1 `updates` loop — evaluate the guard PER ROW rather than once per
  call: a batch where some rows are inside the agent's envelope and some are not books the
  former and reports the latter as row-scoped errors (`envelope_refused` count + note on the
  add side), never an all-or-nothing abort. This was a deliberate design decision beyond the
  literal "wire `_add_one`/`_update_one`" instruction — a per-call-only check would have left
  the envelope trivially bypassable by submitting N>1 rows in one call, which is exactly the
  kind of gap this whole effort exists to close.
- `delete_transactions_where` (hardline-blocked, `HARDLINE_UNATTENDED_TOOLS`) now also calls
  `enforce_unattended_write` before its filtered delete runs — an unattended caller is refused
  unconditionally, no envelope math even evaluated, matching the ADR's own hardline tier.
- **Deliberately left unwired**: `_update_legacy_bulk` (the pre-merge `bulk_update_transactions`
  alias-only shape — one atomic multi-id service call, not a per-row loop, so it doesn't share
  the same per-row call site the rest of this lane reused) and `apply_payee_rules` (lives in
  `ai/tools/payees.py`, out of this lane's file scope — a sibling concern).
- Interactive/connector/skill turns are provably unaffected: `enforce_unattended_write`
  self-guards on `is_unattended()` with no second predicate of this lane's own invention, and
  `tests/test_ledger_unattended_write_wiring.py` pins a turn with an active agent whose
  envelope would refuse everything still writing normally when `is_unattended()` is false.

Verified: `tests/test_ledger_unattended_write_wiring.py` (13 new tests: inside/outside envelope
for both add and update, the hardline block via a real `delete_transactions_where` call with a
wide-open envelope, interactive-turn non-interference for both tools, mixed-batch partial
results for both add and update, no-envelope fail-closed, and the email-transaction-logger
grandfather — all via the real `ai.tools.ledger` entry points, not the isolated primitives).
Every pre-existing test in `tests/test_ledger_confirm_gate.py`, `tests/test_confirm_gate_policy.py`,
`tests/test_unattended_write_envelope.py`, and `tests/test_unattended_tool_safety.py` stays green
unchanged.

**Provenance, Update 2026-07-19c:**

- **Decided by:** senior-engineer lane (dispatched sub-agent), closing this ADR's last open
  Follow-up.
- **Lane / branch:** `lane/wire-unattended-write-guard`, worktree
  `.claude/worktrees/agent-a7112d66cdee13c56`, branched from `harness/reliability-program`.
- **Alternatives weighed:** per-call vs. per-row envelope evaluation for the two true batch
  paths — per-row chosen because a per-call check is trivially bypassable by batching (see
  above); whether to also wire `delete_transactions_where` even though only `_add_one`/
  `_update_one` were named — wired anyway since the tool lives in this lane's own file and the
  fix is a five-line, self-contained hardline check with no envelope math to get wrong.
- **Driving artifacts:** this ADR's own Follow-ups (item 2); `services.agents.resolve_unattended_envelope`'s
  existing grandfather table (consulted, not re-designed); `tests/test_unattended_write_envelope.py`
  (the pre-existing proof of the primitives this lane wired against, left untouched).
- **Why this way (one line):** the built machinery was correct and tested but inert — the only
  change needed was the one call site every write path was missing, done here without touching
  the policy math itself.
