---
title: A provenance ref is attached by a tool and detached only with a "different" decision; provider corrections are applied by the sync automation, not a pipeline
status: Accepted
date: 2026-09-07
deciders: repo owner (decision) + orchestrating Claude agent (design)
---

# 2026-09-07. A provenance ref is attached by a tool and detached only with a "different" decision; provider corrections are applied by the sync automation, not a pipeline

## Context

`2026-09-05-connectors-are-data-sources-lucius-books` left two loops open. First: when a fetched
provider row strongly matches a transaction the user typed by hand, the add choke refuses it
naming the collision (the same bar the dedup engine trusts for auto-merge), but nothing links
the provider ref to the hand-typed row, so the row comes back unbooked on every run inside the
lookback and is reported every time. Email has the same gap, masked by its one-day window, and
its seeded prompt tells Lucius to skip such rows. Second: the fetch tool reports `modified` and
`vanished` rows in `changes[]` but nothing applies them.

The Inbox is hidden, so a pending review card is not a place a decision can live. The
primitives already exist: `update_transactions` accepts `external_refs` and the ledger appends
them (append-only, audited, snapshot-promoted on update since the previous ADR), and it accepts
`tags` and `needs_review`. Provenance removal was an explicit open question in
`ledger.resolve_external_refs_patch`: a caller asking for a removal silently gets its refs back.

## Decision

We will make the attach an explicit, narrowly named ledger write, teach it through the refusal
text rather than the user's automation prose, and allow detaching a ref only as a user action
that records a "different" decision:

- A new tool `add_transaction_refs(transaction_id, refs=[{source, ref}])` appends provenance to
  one existing row. It refuses a pair a live row already holds, naming the holder; refuses a
  deleted target; is idempotent for a pair the row already carries; promotes the matching
  snapshot; writes an `attach_refs` audit entry. Name-keyed, so the owner can set its permission
  level independently of `update_transactions`.
- The duplicate refusal's hint gains a third exit that names this tool with the matched id and
  the incoming ref pair filled in. The connector templates say nothing about duplicates; the
  email prompt's "skip a matching row" sentence becomes "link it to that entry".
- A ref can be detached from a row (`POST /api/transactions/{id}/refs/detach`, and the drawer's
  "Unlink source"), audited as `detach_ref`. Detaching records a `different` decision keyed on
  the external identity `source:ref` and the row in the existing dedup decision memory, and the
  add choke's strong-duplicate guard skips a candidate that carries such a decision, so the next
  fetch books the provider row on its own instead of re-colliding. The append-only patch stays
  append-only; detach is the one deliberate removal path.
- Provider corrections are applied by the sync automation itself with existing tools. Each
  `changes[]` entry carries the ledger's `current` provider-owned values beside the snapshot's
  `before`, plus `user_edited` (true when they differ), so the template's line "bring it in line,
  but never override something I changed by hand; if it withdrew one, flag it and tell me; never
  delete anything" is intent the model can honour without a merge routine. No apply tool.

## Consequences

### Positive

- The re-reporting loop closes for connectors and email with one tool, one hint, one sentence.
- A hand-typed row becomes a first-class linked row: it shows its source and is covered by
  modified/vanished detection.
- A wrong link is reversible in one tap, and the reversal teaches the guard.
- Corrections need no new write surface; the audit trail holds previous values.

### Negative

- Whether a strong match is "the same event" is now decided by the model in an unattended run.
  The bar is the engine's own auto-merge bar and the collision facts are in the refusal, but it
  is a model decision, not code.
- "Never override something I changed by hand" is model-followed intent, enforced only by the
  `user_edited` annotation and the audit trail; a precondition on the update tool is the next
  step if it is ever ignored.
- Provenance is no longer strictly append-only; detach is a second write path to reason about.
- Attaching a provider ref gives the row an emitter (`services.ledger._stamp_ref_emitters`),
  so the same-emitter veto then treats every FUTURE row from that feed as a distinct event
  relative to it. Provider id churn (a re-issued/rotated provider transaction id for the SAME
  real-world event, with no supersedes pointer back to the original) can therefore double-book
  against a row that, before the attach, the strong-duplicate guard would have refused on
  content alone.

### Follow-ups

- If a false attach recurs for the same pair, add a tool that records "different" without
  detaching (today: book with `force` in chat).
- Precondition support on `update_transactions` if `user_edited` is ignored in practice.

## Alternatives considered

- **Pending "attach" review in the Inbox** — rejected: the Inbox is hidden; a decision with no
  surface is a decision nobody makes.
- **Auto-attach in the add choke (deterministic)** — deferred: safe at the strong bar and what
  Actual Budget does, but it removes the model's second look at the facts; kept as the fallback
  if the tool-driven attach misbehaves.
- **A dedicated apply-changes write tool with a three-way merge** — rejected as unnecessary: the
  read tool can expose `current`/`user_edited`, and the generic update already audits.
- **Remember refusals on the snapshot and stop re-returning the row** — rejected: ends the noise
  but leaves the hand-typed row unlinked forever.

## Agent provenance

- **Decided by:** repo owner, in a design conversation with the orchestrating Claude agent
- **Lane / branch:** `feature/connectors-as-data-sources` (PR #68), worktree
  `.claude/worktrees/connectors-as-data-sources`
- **Alternatives weighed:** Inbox review · choke auto-attach · apply tool · refusal memory
- **Driving artifacts:** ADR `2026-09-05-connectors-are-data-sources-lucius-books`,
  `.claude/rfcs/connectors-as-data-sources.md` §I, `core/errors.duplicate_hint`,
  `ledger.resolve_external_refs_patch` docstring's open question
- **Why this way (one line):** the attach primitive already existed; what was missing was a
  name the model can be told, and a way back.
