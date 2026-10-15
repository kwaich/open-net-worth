---
number: 2026-09-26
title: Assets are created in four short steps and edited section by section on their own page
status: Accepted
date: 2026-09-26
deciders: repo owner (product calls "D" and "option 1") + orchestrating Claude agent (ui-designer spec)
---

# Assets are created in four short steps and edited section by section on their own page

## Context

ADR 2026-09-06 ported the Assets prototype's modals faithfully: a two-door chooser, one long Add
form with "More details", and one big Edit dialog. The owner found the flow verbose and
unintuitive; a 2026-09-26 walkthrough measured it: Add opens on a detour ("Enter it manually /
Describe it to Lucius"); Edit is a *different* form from Add (adds Description and Category, shows
the loan picker for every category, turns Add's guided property/vehicle fields into anonymous
"Facts") and carries ~184 words of explanation to Add's 17, scrolling 1.5 screens on desktop and
1.8 on a phone; every action on the asset page, including the repeated Update value, hides behind
an unlabelled `•••`; delete has two names in two places; every dialog closes before the server
answers, so a rejected save loses the user's input.

Asked what matters on the day an asset is added, the owner chose **"net worth now, the record
later"**, and for the shape, **short steps to add and the asset's own page as the place you edit**.
The Accounts tab already sets the precedent of a short create with details afterwards
(`AccountModal`).

## Decision

We will:

1. **Create in four steps in one dialog** whose width stays fixed and whose height fits each step, titled by the step's question with a four-segment progress bar (desktop dialog, phone sheet): *What is it?*
   (category tiles; Lucius as a quiet link, the chooser screen retired) → *Name and value* →
   *Is it all yours?* (share, who with, and — for property, vehicle and custom categories — the
   loan, including staging a new loan that is created in Accounts only when the asset is saved) →
   *What did you pay?* (optional; "What have you paid in?" for policies). Creation asks only what
   net worth needs.
2. **Edit on the asset page, one section at a time** — Ownership, Purchase, Details, Notes — each
   with its own small editor built from the *same* component as the matching create step, plus
   "Rename or change category" in `•••`. The single Edit dialog is retired.
3. **Keep the one repeated action visible**: Update value sits with the Value stat; `•••` holds
   only Rename or change category, Sell or dispose, and Delete asset.
4. **Never lose input on failure**: every dialog waits for the server, keeps its fields, and shows
   the server's own sentence next to the field it names.
5. **Say "value", never "mark"**, in user-facing copy.

This supersedes, for the Add and Edit surfaces only, ADR 2026-09-06 Decision 1's instruction to
port the prototype's modals verbatim. Everything else in that ADR stands.

## Consequences

### Positive
- Adding a watch is four taps plus a name and a value, with no scrolling inside a step.
- Add and Edit can no longer drift apart: the section editors *are* the create steps.
- The record (details, insurance, notes, documents) is filled in where it is read.

### Negative
- More small components to maintain than one Add and one Edit form.
- Recategorising and renaming move into `•••`, one tap deeper than before.
- The Assets tab's Add/Edit surfaces no longer match the Claude Design prototype.

### Follow-ups
- Acceptance criteria AS-07…AS-18 rewritten and AS-23+ added in `docs/qa/acceptance/assets.md`;
  `qa/browser-use/specs/assets_tab.yaml` in step.
- UI spec of record: `docs/assets-redesign/CREATE_EDIT_UI_SPEC.md`.

## Alternatives considered
- **Short steps to add, one tabbed Edit dialog** — rejected: still a big dialog, tabs are cramped
  in a phone sheet, and nothing on the page invites filling the record in later.
- **One quick form to add, full Edit later (the Accounts shape)** — rejected: not step-by-step, and
  the full Edit dialog is the part the owner found heavy.
- **Keep the faithful port and trim copy** — rejected: the Add/Edit mismatch and the hidden
  actions are structural, not copy.

## Agent provenance

- **Decided by:** repo owner (answers "D" and "option 1" on 2026-09-26), carried out by the
  orchestrating Claude agent with the ui-designer's build-ready spec and a user-advocate review.
- **Lane / branch:** `lane/assets-create-edit`.
- **Alternatives weighed:** stepped create + section editing vs tabbed Edit vs quick-create + full Edit.
- **Driving artifacts:** PRD `docs/prd/assets-create-edit-redesign.md`, UI spec
  `docs/assets-redesign/CREATE_EDIT_UI_SPEC.md`, the 2026-09-26 walkthrough screenshots.
- **Why this way (one line):** it is the only shape where "fill in the record later" is built into
  the asset page instead of hidden behind an Edit button, and where Add and Edit share one set of
  components.
