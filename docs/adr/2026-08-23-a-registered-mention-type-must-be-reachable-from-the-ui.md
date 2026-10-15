# A registered mention type must be reachable from the UI

- **Date:** 2026-08-23
- **Status:** Accepted
- **Agent provenance:** Written by Claude (Opus 5) during an owner-driven E2E sweep of every
  tab for mentionables / right-click "Ask Lucius" / drag-into-dock. The owner's report was
  "commitments are not working, and a lot of others are not working" — the sweep found the
  shared cause and this ADR records the gate that stops it recurring.

## Context

Making an entity `@`-mentionable is a **two-sided** contract, and the two sides live in
different languages, different directories, and different test suites:

| Side | Where | What it buys |
|---|---|---|
| Backend | an `EntityDescriptor` registered under `services/mentions/descriptors/` | the type is *searchable* and *resolvable* — `@` picker rows, `resolve_mentions`, labels |
| Frontend | a row stamping `data-entity-id` (+ `draggable`) | the type is *reachable* — right-click "Ask Lucius about this", drag-into-dock, "on this screen" harvest |

`components/chat/lucius/dragRightClick.tsx` binds its listeners **globally and by attribute
selector** (`[data-entity-id]`, `[data-entity-id][draggable]`). That is a good design — a row
opts in declaratively and gets all three interactions for free — but it means the *absence* of
the attribute is silent. Nothing throws. Nothing renders wrong. The right-click menu simply
never appears, and the drag simply does nothing.

Registering a descriptor is the visible, satisfying half of the work, and it is the half that
existing tests cover. So the backend half kept shipping without the frontend half:

- `automation` — found by hand 2026-08-11, fixed with `tabs/automations/lucius.entityWiring.test.tsx`
- `commitment` — found by hand 2026-08-23 (this sweep). A complete descriptor
  (`descriptors/new_types.py`, order=16) with search fields and a sublabel function; the
  Commitments list rendered a plain `<button className="c2-row">`. Additionally the detail
  screen registered no facts of its own, so opening a commitment left Lucius reading
  *list-level* facts with no indication of which commitment was on screen.
- `skill`, `watchlist` — found in the same sweep, identical shape.

Three independent hand-discoveries of one repeating defect is not a run of bad luck; it is a
missing gate.

A related dead end found by the same sweep: `ui/SummaryBar` accepts a `mention` prop that
renders an "Ask Lucius about this" affordance on a KPI card, and **no caller anywhere passes
it**. The affordance has never shipped on any stat card in the app. That is recorded here as a
known gap rather than fixed, because it is a design question (which figures deserve to be
entities) rather than a wiring omission.

## Decision

**Every type registered in `services/mentions` must have a frontend emitter, or be explicitly
exempt with a stated reason.** Enforced by `tests/test_mention_type_frontend_coverage.py`,
which parametrizes over the live `REGISTRY` and scans `frontend/src` for the three sanctioned
emit routes (`entityRefProps(...)`, a literal `data-entity-id` prefix, or a `type="…"` prop in
a file that imports `EntityRow`/`EntityList`).

The test is deliberately a **coarse net**. It asserts that *somebody* emits the type, not that
the wiring is correct. The per-component `lucius.entityWiring.test.tsx` files remain the
behavioural half, asserting the exact attributes `dragRightClick` binds on. This split is the
point: the coarse test catches "nobody wired this anywhere" (the defect that actually recurred
four times), and the local tests catch "the wiring is subtly wrong".

An `EXEMPT` entry requires a reason, and a separate test fails if an exemption names a type
that is no longer registered. Adding a type to `EXEMPT` to make the suite green is the exact
mistake the file exists to prevent, and the module docstring says so.

### Exemptions as accepted

Four are **derived selectors** with no list row to render — `net_worth`, `returns`,
`budget_month`, `report`. They resolve fine when the model names them and ride the screen
snapshot as facts.

One is different and worth calling out: **`target` is exempt because savings targets have no
UI at all.** `TargetsStore`, `services/targets.py`, `GET|POST|PUT|DELETE /api/targets` and the
mention descriptor all exist; `frontend/src` contains no `Target` type, no `/api/targets`
caller, and no screen. This is an unbuilt feature, not a wiring gap — wiring a row would mean
inventing the UI. The exemption is the honest record of that, and it should be deleted the
moment a targets screen ships.

## Consequences

- Adding a descriptor without a UI row now **fails CI** with a message naming the type, the
  consequence ("cannot be right-clicked, dragged, or harvested"), the fix, and the
  `EXEMPT`-with-a-reason escape hatch.
- `ui/Panel` now forwards rest props onto its host `<section>` so it can serve as an entity
  host (needed for the Inbox notification row). Additive — every existing caller renders
  byte-identically.
- Two wirings are **conditional**, and both conditions are tested including the negative case:
  a notification is emitted only for `kind === 'signal'` inbox rows (the kind actually backed
  by `NotificationLogStore`), and a price alert only when it carries an `id`. Neither emits an
  unresolvable ref when the condition fails.
- `EntityRow`'s header comment claimed "DO NOT adopt in the 9 existing emitters yet — BUILD
  ONLY in Phase 1". That has been false for some time (it is adopted in a dozen-plus files);
  the sweep treated adoption as the norm, which this ADR ratifies.

## Alternatives considered

**Make the descriptor itself declare its UI location.** Rejected: the descriptor lives in
`services/`, the row lives in a `.tsx`, and coupling them would either put frontend paths in
backend code or require a build step to verify. The grep-based gate needs no coupling at all.

**Assert per-type in the component tests only.** Rejected: that is the half that already
existed and still let three types through. A per-component test only exists once somebody has
already noticed the type needs wiring — which is precisely the step that kept being skipped.
The registry-driven parametrization is what makes the check exhaustive by construction.
