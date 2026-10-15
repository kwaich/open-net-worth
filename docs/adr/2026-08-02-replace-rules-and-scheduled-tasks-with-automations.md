---
number: 2026-08-02-replace-rules-and-scheduled-tasks-with-automations
title: Replace Rules and Scheduled Tasks with one Automation fact
status: Accepted
date: 2026-08-02
deciders: Codex orchestrating agent + repo owner
---

# 2026-08-02. Replace Rules and Scheduled Tasks with one Automation fact

## Context

The product exposes two independently persisted automation concepts. Rules are
ordered, synchronous transaction transformations used inside ledger and statement
import write chokes. Scheduled Tasks are time-driven rows with several deterministic
executors plus a headless Lucius executor. They have separate stores, services, REST
routes, AI tools, mention types, and frontend editors.

The approved Automation handoff defines one user-owned entity with plain-English
instructions and either a schedule or event trigger. The owner explicitly rejected
a compatibility UI, a preserved Rules capability, a “Run by the app” grouping, and
backward-compatible code. Existing live Rules and Scheduled Tasks must migrate, not
disappear. Money correctness still requires deterministic transaction transforms to
run at their current pre-commit phase, and a migration must write and validate the new
fact before retiring either source file.

Automation executions also have mutable lifecycle states and durable conversations.
The shared `RunHistoryStore` is append-only and coerces unsupported live states, so it
cannot safely represent an Automation waiting for a real approval card.

## Decision

We will replace Rules and Scheduled Tasks with one canonical Automation model,
`automations.json`, one service mutation choke, one REST surface, one AI-tool family,
one mention type, and one frontend route. There will be no production reader, writer,
alias, route, component, or product label for either legacy system after cutover.

An Automation has a `manual`, `schedule`, or `event` trigger. Its public shape is the
same regardless of origin. A private, validated execution union preserves reliable
semantics: Lucius-backed work, deterministic notifications/transactions/reminders,
agent invocation, and transaction transforms all remain implementation details rather
than visible product types.

Migrated Rules become transaction-event Automations whose compiled transform executes
at the exact former ledger and statement-draft phases. Their match, priority, stop,
override, route-to-account, category, tag, note, and audit semantics are preserved.
Matched transforms write their run/statistics only after the surrounding commit
succeeds. Migrated Scheduled Tasks retain their schedule and executor semantics.

The preserved transaction semantics are exact: `direction` remains leg-based
(`debit` when a from-leg exists, including a two-legged transfer; otherwise `credit`
when a to-leg exists), and `stop` fires when the conditions match even when an action
is already satisfied or blocked by category precedence. A match therefore carries a
separate `category_applied` fact. The last transform that actually applied the category
owns preview and audit provenance; tag-only and blocked category actions never claim
that field, while a deliberate same-value override remains visible in preview. Single
and bulk creates both call `resolve_transaction_row`; bulk and bulk preview pass one
preloaded Automation, category, account, FX, and base-currency snapshot through every
row so their resolution and metadata cannot drift.

We will add a separate mutable `AutomationRunStore` with an explicit state machine:
`running -> waiting -> running -> completed|failed`, or directly from `running` to a
terminal state. Every execution after cutover gets a durable run and conversation;
deterministic executions use a zero-cost synthetic transcript that can later continue
with Lucius. Historical runs without source transcripts remain sessionless and are
reported honestly as unavailable.

The one-shot migration is mandatory, dry-run-first, idempotent, fail-closed, and
recoverable. It freezes legacy input decoders inside the script, writes and validates
Automations plus rewritten references and Automation runs, then moves the two source
files into a hashed timestamped backup. Runtime code never imports those decoders or
falls back to the old files.

## Consequences

### Positive

- The UI, APIs, tools, storage, and execution vocabulary describe the same product.
- Event-driven and schedule-driven work have equal status and one lifecycle.
- Transaction categorization and statement routing preserve their pre-write guarantees
  without exposing a second Rules subsystem.
- Live/waiting execution states cannot be silently coerced by the agent run ledger.
- Every new execution has a durable, resumable conversation and exact persisted usage.
- The cutover can be audited and rolled back from a byte-identical data backup.

### Negative

- The migration rewrites several referential JSON files and is intentionally not
  backward compatible; rollback requires restoring the backup together, not starting
  old code against partially migrated data.
- Private deterministic executor variants add internal model complexity even though
  the public product has one entity type.
- Synthetic conversations and per-Automation run history consume storage and require
  referential pruning.
- Removing old tool aliases means persisted scopes and playbooks must be migrated in
  the same cutover or fail validation.

### Follow-ups

- Implement the canonical stores, service package, event runtime, runner seam, API,
  tools, frontend, and one-shot migration specified in the RFC.
- Rename transaction audit provenance from `rule_applied` to
  `automation_applied`, including historical payloads.
- Delete all legacy Rules/Scheduled Task modules and enforce their absence with static
  searches and endpoint/tool registry tests.
- Verify the owner’s current 4 Rules plus 16 Scheduled Tasks reconcile to exactly 20
  Automations before applying the live migration.

## Alternatives considered

- **One UI over both legacy backends** — rejected because it leaves two facts, two
  mutation paths, incompatible run semantics, and a product surface that lies.
- **Keep Rules as a separate existing capability** — explicitly rejected by the owner;
  Rules must migrate into transaction-event Automations.
- **Convert every row into a Lucius prompt** — rejected because model-selected writes
  would weaken deterministic ledger, import-routing, and recurring-money guarantees.
- **Reuse `RunHistoryStore`** — rejected because its append-only terminal schema cannot
  represent or safely transition running and waiting Automation executions.
- **Maintain aliases and fallback readers** — rejected because the owner requested a
  hard migration and because dual readers make a partial migration ambiguous.

## Agent provenance

- **Decided by:** Codex orchestrating agent, informed by solution-architect,
  UI-designer, and backend/frontend explorer lanes; ratified by the repo owner
- **Lane / branch:** `feature/automations-redesign`
- **Alternatives weighed:** legacy facade, preserved Rules sibling, all-Lucius
  execution, shared run history, and compatibility aliases
- **Driving artifacts:** [PRD](../prd/unified-automations.md),
  RFC,
  [UI specification](../superpowers/specs/2026-08-02-unified-automations-design.md),
  and `frontend/design_handoffs/automation_v3_handoff/`
- **Why this way (one line):** One public fact removes the product split while private
  deterministic executors preserve the money and import invariants the split used to
  protect.
