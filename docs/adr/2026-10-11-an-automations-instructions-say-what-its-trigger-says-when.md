---
title: An Automation's instructions say what; its trigger says when
status: Accepted
date: 2026-10-11
deciders: Repo owner, Claude
---

# An Automation's instructions say what; its trigger says when

## Context

The editor asked people to write instructions "including when it should run", and a model call
(`POST /api/automations/infer`, `services/automations/inference.py`) read the timing back out of
the words on every pause in typing to pre-fill the trigger — plus the model, effort, approval
mode and description. That stored one fact twice. The pre-fill only ran text → trigger, so
changing the trigger later left the words behind: "When a charge over 500 posts…" beside a
trigger at 1,000, "summarize yesterday" on a schedule moved to weekly. The run never saw the
configured rule either — only `{"kind": "event", "event": "txn"}` or `{"kind": "schedule"}` and
its payload — so it could not reconcile the two. The pre-fill was also narrower than the editor
(no payee, "under", memo, "is not"), so "when I pay Grab" came back as *Description contains*.

## Decision

- **Instructions say what Lucius does; the trigger alone says when.** The pre-fill endpoint and
  service are removed; the editor shows the trigger fields under **When it runs** instead of
  behind *Edit trigger details*, with the trigger read back as a sentence above them.
- **Every run is told the rule and the last completion**: `configured_trigger` (the stored
  trigger plus `names` for the accounts and payees it points at) and `last_completed_run`
  (`{"started_at"}` of the latest prior run that COMPLETED — a failed run may have done nothing)
  ride in the run context (`services/automations/run_context.py`). Instructions written as
  "since the last run" stay right whatever cadence is picked.
- Templates carry no timing (pinned by `automationTemplates.test.ts`); Lucius's
  `create_automation` instructions field says the same. The log-transactions-from-email skill
  fetches since the last completed run (at least one day) instead of a fixed day sized for a
  12-hour interval.
- Naming a subject stays possible for Lucius: accounts and payees in conditions resolve by id or
  exact name at the save choke (`services/automations/references.py`), refusing an unknown or
  ambiguous one.

## Consequences

- One place to change when an Automation runs; nothing to keep in sync.
- Automations already created from a template keep their copied text ("Every 12 hours, …") —
  harmless until their trigger changes; they are not rewritten.
- Model, effort, approval mode and description are no longer guessed from the text; each has its
  own control, and the description still defaults to the instructions' first sentence.
- `automation_inference` stays a cost source so recorded spend keeps its bucket.
