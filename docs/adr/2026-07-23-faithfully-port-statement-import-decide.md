---
title: Render the Statement Import handoff over the existing production decision pipeline
status: Accepted
date: 2026-07-24
deciders: Codex orchestrating agent + repository owner
---

# Faithfully port the Statement Import Decide handoff

## Context

PR #35 preserved and extended important import behavior, but explicitly chose not to port the
Statement Import v3 visual hierarchy. The owner rejected that choice. A later partial pass added
the handoff's compact closed-row rails, but it kept the rejected three-column expanded card,
the legacy table-based plain rows, the old filter taxonomy, and the old action vocabulary. Its
tests explicitly locked those choices.

`ImportModal.tsx` remains the only production flow and owns real upload, parsing, account
detection, reconciliation, decisions, commit, errors, audit, and review completion. The
prototype is the visual and interaction specification; its mock scenarios and mock writes are
not production contracts.

## Decision

We will port the prototype's Decide-step DOM hierarchy and interaction model over the existing
production state:

- facts → two-question verdict → Lucius note → sticky filters;
- a named, statement-ordered audit section using one semantic row component;
- a real Needs-you queue whose cards remain expanded after a choice and support Undo and the
  prototype's `1`/`2`/`3` keyboard choices;
- matched rows that expand to `On the statement | In your ledger`, one consistent decision
  radiogroup, then one full-width `After import` result;
- explicit Left out rows with visible reasons and bordered override actions;
- compact ledger-only disclosures and the handoff footer/Booked treatment.

Every statement row has one effective filter owner. Review, weak, and read-check rows stay in
`Needs you` after a choice so their receipt and Undo do not disappear; other rows move with the
effective outcome. The two-question final verdict remains gated until every actionable queue
card has an explicit receipt, even though the factory review recommendation is `merge`.

For detected two-leg transfers, the OUT leg owns the active import/link decision only while the
pair has no ordinary expense/income match. Its IN partner renders `Folded into transfer` only
when a new or existing two-leg transfer represents both legs. A non-transfer match on either leg
splits the pair into visible decisions; the decision service honors every explicit IN-leg action
instead of absorbing it. Low-confidence, Lucius-skip, and pre-reconciliation guards run before
any separate IN creation and emit an explicit skip for a split pair. Leaving out an unmatched
OUT leg makes both rows explicitly Left out. Booking a clean IN leg separately emits the existing
explicit create deviation and does not create a second import path.

The existing production state and callbacks remain authoritative through a view adapter; no
parallel importer, mock service, or raw write path is introduced. Backend action values remain
`merge|create|skip`, while the UI uses the handoff vocabulary (`Link & update|Keep both|Leave
out`). The canonical `Modal`, `TxnDetail`, and production theme tokens host the port. Responsive
rules adapt the fixed desktop rails at the actual review-container width without hiding facts.

All required backend surfaces are classified **EXISTS**: statement preview/commit, detection,
dedup/external-reference protection, enrichment, category edits, manual match override,
undo-to-Lucius, reconciliation, errors, audit/checkpoint/validation, and batch review marking.
Preview books no transactions, but it intentionally persists the content-addressed Statement
record/cache and optional format/instruction memory. The port adds no backend write surface; it
does correct the existing commit fold so an explicit non-transfer IN-leg decision breaks a
parser-level pair and reaches the same merge/create/skip pipeline as every other row.

## Consequences

### Positive

- The live flow, not a prototype artifact, becomes visually faithful to the handoff.
- Financial behavior remains behind the existing preview/decision-commit and ledger choke points.
- One semantic statement-ordered list makes decision, ledger evidence, and outcome scannable.
- The same semantic structure supports desktop, tablet, mobile, light, dark, and assistive tech.

### Negative

- The production-to-prototype adapter must preserve a large, mature state model.
- Pixel parity needs intentional responsive adaptations because the handoff has no mobile rules.
- Existing tests coupled to the rejected three-column/table DOM must be replaced while retaining
  their financial and decision-payload assertions.

### Follow-ups

- Keep `frontend/design_handoffs/statement_import_handoff/PARITY_AND_GAPS.md` as the evidence map.
- Maintain browser acceptance coverage and reference/production screenshots for the touched flow.

## Alternatives considered

- Preserve the current three-column cards and borrow only closed-row copy/colors — rejected by
  the owner and by the port skill because it repeats PR #35's failure.
- Copy the prototype and its mock engine wholesale — rejected because it would create a parallel
  financial workflow and regress production correctness.
- Paste the bundled design-system stylesheet — rejected because production primitives/tokens
  already provide the shell and the task explicitly forbids the 17,000-line bundle.

## Agent provenance

- **Decided by:** Codex orchestrating agent
- **Lane / branch:** `mc/124-faithfully-port-the-statement-import-des`
- **Alternatives weighed:** current-card preservation, mock-engine copy, verbatim view over real state
- **Driving artifacts:** task 124; complete `frontend/design_handoffs/statement_import_handoff/`
  bundle and interactive prototype; PR #35
- **Why this way (one line):** It is the only approach that makes the prototype the visual spec while keeping the production importer the behavioral authority.
