# UI Quality Gate

The single, **blocking** standard a frontend-touching change must clear before it
merges. It exists because the failure mode that hurt most was UI and interactions
shipping *unverified* — a control rendered, the change compiled, `tsc`/build went
green, and nobody ever clicked the button. "It compiles" and "it renders" are NOT
"it works." This gate makes that impossible to skip.

> Decision of record: [ADR-0001](adr/0001-adopt-adr-and-ui-quality-gate.md).
> UI conventions (how to build): [FRONTEND_CONVENTIONS.md](FRONTEND_CONVENTIONS.md).
> Acceptance-criteria contract: [qa/acceptance/README.md](qa/acceptance/README.md).

## The three-phase loop (design-first → build → verify)

Every non-trivial UI change runs three explicitly-owned phases. The front half
(design) was already strong; the back half (verify) is what this gate adds.

| Phase | Owner | Output |
|---|---|---|
| **1. Design** | `ui-designer` (read-only) | A wireframe + build-ready spec: placement (new tab vs extend vs sub-tab), `ui/` primitives + shared components to reuse, layout/spacing tokens, labels, and **empty/loading/error states**. No code. |
| **2. Build** | `senior-engineer` / `junior-engineer` | The implementation, following the spec and FRONTEND_CONVENTIONS.md. Plus an **acceptance file** (below) for the new/changed behavior. |
| **3. Verify (BLOCKING)** | `qa-engineer` **and** `ui-designer` (verify-build mode) | Two independent verdicts against the *running* app — see below. Both must pass. |

The verify phase has two distinct owners so nothing falls between them:

- **`qa-engineer` — interactions & behavior.** Drives the running app and exercises
  every new/changed interactive control: click it, type into it, submit it, and
  assert the *observable outcome* (the value changed, the row appeared, the total
  reconciled). "It rendered" is not a pass. Classifies each failure real-bug vs infra.
- **`ui-designer` (verify-build mode) — visual & spec conformance.** Compares the
  rendered build to its own spec region-by-region: placement, primitive reuse,
  spacing tokens, empty/loading/error states, and reflow at 640 / 1024 / 1440 with
  the chat panel open AND closed.

## The blocking merge checklist

A change that touches `frontend/` (or an API shape a tab renders) does NOT merge
until ALL of these hold:

1. **Acceptance file exists and is interaction-level.** Every new or changed
   interactive control has a GIVEN/WHEN/THEN criterion in
   `docs/qa/acceptance/<feature>.md` (the anti-"dead button" rule —
   see qa/acceptance/README.md). A new control with no covering criterion = not done.
2. **The deterministic UI gate is green** for the touched criteria:
   `make qa-e2e` (browser-use; boots the app on a high port against secrets-stripped
   cloned data and drives a real headless browser). Exits non-zero on any failure.
3. **Screenshot evidence is attached.** Cite the artifacts from
   `qa/browser-use/artifacts/` (and/or a `make qa-cu` run) in the PR / merge note —
   the reviewer sees the rendered result, not a promise.
4. **`make ui-gate` is clean** for the touched tab — no new horizontal overflow,
   no console errors, no chat-panel overlap, at 3 widths (it self-boots a hardened
   disposable instance; never touches `:8080` or live data).
5. **Empty / loading / error states are present and verified**, not just the happy path.
6. **CSS discipline holds** — no new `className` token without its definition in
   `frontend/src/index.css`; `npm run lint:classes` passes (part of `make build`).

For a *non-trivial* feature, also run `make qa-cu` (Claude computer-use) as an
on-demand acceptance + UX pass — does the flow actually work and read right.

## Which command, when

| Command | What it proves | When |
|---|---|---|
| `make build` (`lint:classes` + `lint:entity-rows` + `lint:tokens` + `lint:breakpoints` + `vite build`) | CSS class/token/breakpoint discipline holds, production bundle compiles — **does NOT typecheck** (`vite build` strips types; that's `make type`'s job) | every change / CI |
| `make type` (`mypy backend` + `tsc` frontend, both blocking) | Types actually check | every change / CI |
| `make ui-gate` | Geometry: no overflow / console-errors / chat-overlap at 3 widths (self-booting, isolated) | while iterating on a tab; before requesting verify |
| `make qa-e2e` | The acceptance criteria PASS in a real browser, deterministically | **the UI merge gate** |
| `make qa-cu` / `make qa-cu-mock` | The feature works end-to-end and feels right (vision agent) | on-demand after a non-trivial feature |
| `make check` | The full local gate this table's rows are drawn from, in one shot: `lint → type → test → build → docs-lint` (mirrors CI) | before every push |

This UI-specific checklist is layered ON TOP of `make check` (which also runs
`docs-lint`, the `docs/product/**/*.md` corpus linter — irrelevant to most UI
changes, but still part of the same blocking local/CI gate), not a
replacement for it.

See [qa/browser-use.md](qa/browser-use.md) and [qa/computer-use.md](qa/computer-use.md)
for the harnesses, and the **QA rules** in CLAUDE.md for the data-safety invariants
(QA always runs on a high port against isolated dirs — never `:8080`/`:5174`/live data).

## Definition of UI Done

A UI change is done when: design spec followed · acceptance file written ·
`make qa-e2e` green for its criteria · screenshots attached · `make ui-gate` clean
· empty/loading/error states verified · both verify owners pass. Anything less is
in-progress, no matter how good it looks in a single screenshot.
