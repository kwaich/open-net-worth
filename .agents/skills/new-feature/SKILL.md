---
name: new-feature
description: The full feature lifecycle for this repo — PRD -> RFC -> implement -> test -> review -> ship. Use when starting non-trivial work (a new tab, tool, endpoint, store, or data-model change) so it follows the project's planning, dependency, test, and DoD rules.
---

# New feature lifecycle

Right-size this — a typo fix skips planning/design; a new tab/tool/endpoint/store does the lot. The path (also in `CONTRIBUTING.md` → "PR workflow & Definition of Done"):

```
/feature-planning  ->  /system-architect  ->  implement  ->  test + make check  ->  /code-review (+ /security-review)  ->  ship
   PRD in docs/prd/     RFC in docs/rfc/ or                                         money/auth/external-I/O only
                        .Codex/rfcs/
```

## 1. Plan — `/feature-planning`

Decide scope and the user-facing shape **before** code. Output a short PRD in `docs/prd/`. Optionally pressure-test it with `/user-review`.

## 2. Design — `/system-architect`

Produce a Technical RFC (new RFCs in `.Codex/rfcs/`; `docs/rfc/` holds earlier ones) for anything that touches the data model, money math, the AI tool surface, or crosses layers. Name the core design question, weigh 2-3 options, and state what the choice locks in. Phase-1 RFCs in `.Codex/rfcs/phase1/` are the reference for depth and the citation style. **For a UI change, get a `ui-designer` wireframe + spec FIRST** (design-first). **Record a significant / irreversible / convention-deviating decision as an ADR** — `/adr` → `docs/adr/` — so the WHY is captured, not lost in the diff.

## 3. Implement — to the dependency rule

The one architectural contract (`import-linter` enforces it — `make imports`):

```
api/  ─┐
        ├─►  services  ─►  db  ─►  core
ai/   ─┘
```

- **`core/`** — config, paths, errors, logging, units, fx. Depends on nothing internal; a leaf any layer may read.
- **`db/`** — repositories (`JsonStore` subclasses). Data access only, no domain rules. **Must NOT import `ai.*`** (checkpoint hook is inverted via `core.write_hooks`).
- **`services/`** — **the only place financial math lives** (net worth, P&L, FX, balances). Import-pure (no FastAPI/HTTP types). Depends on `db/` + `core/`. **Must NOT import `api.*`**.
- **`api/`** + **`ai/`** — thin top: validate → call **one** service → shape the response. No money math in a route or a tool head.

Never import another layer's `_private` symbol. **No backward-compat shims** — migrate the data file (script in `backend/scripts/`) instead of reading old + new shapes. Size budgets: functions ≤ ~60 lines, files ≤ ~400.

Use the focused skills for each piece:
- new persisted file / queries → **add-store**
- REST route → **add-endpoint**
- AI/chat tool → **add-tool**
- tests → **write-test**
- validating without a server → **run-app**

## 4. Test

Per `write-test`: store CRUD test, service unit test, endpoint `TestClient` smoke, AI-tool behavioral test; bug fixes ship a regression test. Fast gate green: `make test`. Don't loosen the characterization tests (`tests/test_char_*`) to make a refactor pass.

## 5. Verify — hang-safe

Backend import smoke + `make check` (lint -> type -> test -> build). NEVER boot uvicorn / `npm run dev` in automation — see **run-app**.

**UI changes also clear the UI Quality Gate** (`docs/UI_QUALITY_GATE.md`): an interaction-level acceptance file in `docs/qa/acceptance/`, `make qa-e2e` green for the touched criteria, `make ui-gate` clean, screenshot evidence, and empty/loading/error states verified. "It compiles / it renders" is NOT done — every new control gets clicked/typed/submitted and its outcome asserted.

## 6. Review & ship

`/code-review` on the diff; `/security-review` if it touches money, auth, or external I/O. Branch off `main` (never commit to `main` directly); conventional-commit messages (`feat:`/`fix:`/`refactor:`/`docs:`).

## Definition of Done

1. Lint + type + tests + import-linter green locally and in CI.
2. New/changed behavior tested (bug fixes include a regression test).
3. **Docs updated in the same commit/PR** — `AGENTS.md` / `FEATURES.md` and the relevant RFC/PRD (standing project rule).
4. No secrets, no personal data, no new layering violations. **Stage specific files — never `git add -A`/`.`; never stage `backend/data/*`** (e.g. `price_history.json`, `cost_log.json`).
5. Reviewed (human or `/code-review`; `/security-review` for money/auth/external I/O).
6. **UI changes**: UI Quality Gate cleared — design-first spec, acceptance file, `make qa-e2e` green, `make ui-gate` clean, screenshots attached (`docs/UI_QUALITY_GATE.md`).
7. **Significant decisions**: an ADR recorded in `docs/adr/` (`/adr`).
