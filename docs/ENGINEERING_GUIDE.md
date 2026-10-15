# Open Net Worth — Engineering Guide: Codebase Health, Reorg & Standards

> **Date:** 2026-06-04. An audit of codebase health plus a concrete plan to reorganize, enforce a clean architecture, and install the standards + workflow + Claude-enablement that make the repo workable by **any engineer or AI agent** to an industrial standard.

> **STATUS (2026-08-23): historical audit + roadmap, largely EXECUTED — read as "why the
> rules exist," not as a live status report.** Every finding in §1 and every wave in the
> §3/§8 roadmap describes a codebase state that **no longer exists**: `backend/ai/tools.py`
> (the 1,154-line/2,664-LOC monolith §1.1 names) is now `backend/ai/tools/`, a 69-file
> per-domain registry (the `add-tool` skill); ruff/mypy/pytest/import-linter/CI/pre-commit
> all exist and are CI-blocking (§1.3's "essentially absent" tooling table is fully ✅ today
> — see the root `Makefile` and `.github/workflows/ci.yml`); the dependency rule is
> enforced by 5 import-linter contracts (CLAUDE.md's "Dependency rule" section, not the 3
> sketched in §2.1); `.claude/skills/` now has repo-specific skills (`add-tool`, `add-store`,
> `add-endpoint`, `write-test`, `run-app`, `new-feature`, …) beyond the "only generic
> planning skills" §1.4 describes. For CURRENT engineering standards, dependency rule, and
> CI gate, read `CLAUDE.md` directly (its "Coding standards" / "Dependency rule" /
> "Verification" sections are the living version of what §2/§4/§5 here proposed) and the
> root `Makefile`. This document is kept for its **audit reasoning** (why each rule exists,
> what it fixed) — do not treat any "is missing" / "no X" claim below as current.


---

## 0. Honest assessment

The codebase is **better than "smelly" suggests, but lacks discipline.** Good bones — a clean repository/store layer, real provider abstractions, 113 passing tests, turn-checkpoints, and unusually good RFC/PRD docs. What's missing is **enforcement**: there is no dependency rule, no linter/type-checker/formatter, no documented build cycle, and a few files have grown into god-objects. The result: it *works*, but an AI or a new engineer has no guardrails — nothing stops the next change from adding another 200 lines to a 1,154-line function or importing a private function across layers.

**The fix is not a rewrite.** It's: (1) install tooling + a dependency contract, (2) carve the god-files apart behind tests using the registry patterns already specified in the platform RFC, (3) write down the standards + lifecycle + Claude skills so the rules are executable, not folklore.

---

## 1. Audit — findings (measured, not vibes)

> _Historical, as of 2026-06-04 — every gap below is now closed; see the status note above._

### 1.1 God files & functions

| Symptom | Evidence | Why it hurts |
|---|---|---|
| **`execute_tool` is one 1,154-line function** | `backend/ai/tools.py` (2,664 LOC total), 64× `if name == …` | unreviewable, untestable in isolation, merge-conflict magnet, blows the AI's context when editing |
| Large API/service files | `api/ledger.py` 726, `ai/assistant.py` 675, `monitoring/intel_agent.py` 647, `ledger_service.py` 530, `ai/prompts.py` 529 | mixed concerns; hard to navigate |
| Frontend monoliths | `index.css` **1,030** (all app styles in one file), `Intel.tsx` 911, `Cash.tsx` 826, `chat.css` 548 | no style scoping; tabs mix data-fetch + layout + presentation |

### 1.2 Architecture — no enforced dependency direction (the core issue)

Layering exists informally (`api/` → services → `db/`) but is **violated in both directions**, and no tool catches it:

| Violation | Evidence | Direction |
|---|---|---|
| Data layer imports AI layer | `db/json_store.py` & `db/price_history_store.py` → `from backend.ai.checkpoint import …` | down-dep inversion (db → ai) |
| Service imports API layer | `commitments_service.py` → `from backend.api.prices import load_fx_cache`, `from backend.api.cashflow import _cash_balance_base` | up-dep (service → api) |
| Cross-layer **private** imports | `reconcile.py` → `backend.ai.memory._parse_json`; `commitments_service.py` → `api.cashflow._cash_balance_base` | encapsulation break |
| Services unpackaged at root | `backend/{ledger,positions,commitments}_service.py`, `reconcile.py`, `units.py`, `paths.py` | no clear "services" boundary |

The repo layer's `ai.checkpoint` import is a *deliberate* write-hook, but wiring it via a direct import inverts the dependency — it should be an injected callback (see §2.3).

### 1.3 Tooling — essentially absent

| Tool | Backend | Frontend |
|---|---|---|
| Formatter | ❌ none | ❌ no prettier |
| Linter | ❌ no ruff/flake8 | ✅ eslint (`eslint .`) |
| Type checker (enforced) | ❌ no mypy | 🟡 `tsc -b` builds, but **`tsconfig` has no `strict`** |
| Test runner | ✅ pytest (113 pass / 25 skip, good `conftest` + `integration` marker) | 🟡 vitest configured, **only 2 test files, no testing-library** |
| Pre-commit hooks | ❌ | ❌ |
| Dependency pinning | 🟡 `requirements.txt` ranges (no lockfile) | ✅ `package-lock.json` |
| CI | ❌ none (see roadmap A6) | ❌ |

### 1.4 Process & Claude-enablement

- **No documented dev lifecycle.** Plan/design tooling exists (`feature-planning`, `system-architect` skills → PRDs/RFCs) but there's no written **plan → design → dev → test → review** cycle or **Definition of Done**.
- **`.claude/skills/` has only generic planning skills** (feature-planning, system-architect, user-review). No repo-specific skills encoding *how this codebase does things* (add a tool, add a store, add an endpoint, add a widget, write a test, run the app). So an AI agent re-derives conventions every time.
- **No `.claude/commands/`**; `CLAUDE.md` documents *structure* superbly but has no **"how to write code here"** section (dependency rule, size limits, test policy, DoD).
- **Clutter** in the tree: `backend/dev-data/`, `backend/data_backup_20260603_022916/`, `test_transactions/` (also flagged in roadmap A0).

### 1.5 What's already good (keep)

Repository/store pattern (`db/`), `AIProvider`/`SearchProvider` abstractions, `conftest.py` with an `integration` marker that skips LLM tests cleanly, RFC/PRD culture, turn-checkpoints, no committed secrets, **zero TODO/FIXME debt markers**.

---

## 2. Target architecture

### 2.1 The dependency rule (the spine — everything else serves this)

One direction only. Nothing imports "upward"; nothing imports another module's privates.

```
        ┌─────────────────────────────────────────────┐
        │  api/ (thin routers)        ai/ (agents,      │
        │        │                     tools, prompts)  │
        │        ▼                         │            │
        │     services/  ◄─────────────────┘            │
        │        │     (business logic — the only place │
        │        ▼      financial math lives)           │
        │      db/      (repositories; data only)       │
        │        │                                      │
        │        ▼                                      │
        │     core/     (config, paths, errors, units,  │
        │               fx — zero internal deps)        │
        └─────────────────────────────────────────────┘
   Rule:  api → services → db → core      ai → services → db → core
   Never: services → api,  db → ai/services,  X → Y._private
```

- **`core/` depends on nothing.** **`db/` depends only on `core/`.** **`services/` on `db/`+`core/`.** **`api/` and `ai/` on `services/`** (and may read pydantic models). 
- **Financial truth lives only in `services/`** — routers and agents call services; they never recompute net worth / P&L / FX. (This is also the rule that makes the multi-agent firm safe — Pillar D §D.2.)
- Enforced by **`import-linter`** contracts in CI (§5). The rule is not a guideline; a violating PR fails.

### 2.2 Target directory layout

```
backend/
  core/                 # config.py (typed Settings, DATA_DIR override), paths.py, errors.py, logging.py, units.py, fx.py
  models/               # pydantic domain models / API schemas (Instrument, Position, Transaction, Account, Profile…)
  db/                   # repositories (as today) — minus the ai.checkpoint import (see §2.3)
  services/             # MOVED FROM ROOT: ledger, positions, commitments, reconcile + new: networth, cashflow, fire, fx_cache
  ai/
    agents/             # orchestrator + specialists (Pillar D)
    tools/              # one module per domain + registry.py  (REPLACES the 2,664-line tools.py)
    prompts/            # builder split from the 529-line prompts.py
    models/  search/  assistant.py  checkpoint.py  memory.py
  api/                  # THIN routers only — no business logic; validate → call service → return
  ingest/  monitoring/  connectors/   # as today
  scheduler.py  main.py
frontend/src/
  features/             # vertical slices: networth/, investments/, ledger/, commitments/, intel/, chat/
    <feature>/          #   components, hooks, api, types co-located
  shared/               # widget registry, reusable components, ui primitives
  styles/               # tokens.css + per-feature css (split index.css's 1,030 lines)
  lib/                  # api client (generated from OpenAPI), format, constants
```

Moves are mechanical (root services → `services/`, `paths.py` → `core/`); the value is the **boundary**, then enforcement.

### 2.3 Three structural fixes that unlock the rule

1. **Invert the checkpoint hook.** `db/` should not import `ai.checkpoint`. Define a write-hook protocol in `core/` (`register_write_hook(fn)`); `ai/checkpoint` registers itself at startup; `JsonStore._save_raw` calls the hook if present. db now depends only on core.
2. **Pull FX-cache + cash-balance into services.** `commitments_service` importing `api.prices`/`api.cashflow._private` means those primitives are mis-located. Move `load_fx_cache` → `services/fx_cache.py` (or `core/fx.py`) and `_cash_balance_base` → `services/cashflow.py`. Routers and services both call the service; nobody imports `api/`.
3. **Dismantle `tools.py` via the registry** (already specified — platform RFC §3.1). `ai/tools/<domain>.py` modules with `@tool(...)`; `execute_tool` becomes a registry lookup. The 1,154-line function dies incrementally (if-chain kept as fallback during migration).

---

## 3. Refactor plan — safe, incremental (strangler, test-guarded)

> _Historical — this plan is now COMPLETE (E0–E2 executed; see the status note above)._

**Golden rule: tooling + tests first, then move, then split. Never big-bang.** Each step is independently shippable and green.

**R0 — Guardrails before touching code.**
- Land §5 tooling (ruff, mypy, prettier, strict `tsconfig`, pre-commit) + CI. Auto-format the whole tree in **one isolated commit** (no logic change) so future diffs are clean.
- Add `import-linter` with the §2.1 contracts — initially **as warnings** (record the known violations as a baseline), flip to **failing** after R2.
- Delete clutter: `backend/dev-data/`, `data_backup_*`, `test_transactions/` (ties to roadmap A0).

**R1 — Characterize before carving.** For each god-file target (tools.py, ledger.py, intel_agent.py), add **characterization tests** that pin current behavior (esp. the tool dispatch and ledger math). You can't safely split what isn't tested.

**R2 — Establish boundaries (mechanical moves).** Create `core/`, `services/`, `models/`; move root services in; apply the three fixes in §2.3. Update imports. Flip `import-linter` to failing. _One PR per layer; tests green throughout._

**R3 — Carve the backend god-files.**
- `tools.py` → `ai/tools/` registry (per-domain modules). _Biggest win; do over several PRs, one domain at a time._
- `prompts.py` → `ai/prompts/` (static body / volatile snapshot / per-protocol blocks).
- `intel_agent.py`, `assistant.py`, `ledger.py` → extract pure helpers into services; routers/agents stay orchestration-only.

**R4 — Carve the frontend.**
- Split `index.css` → `styles/tokens.css` + per-feature CSS (or CSS Modules); enables theming (canvas RFC §2.4).
- Reorganize `components/tabs/*` into `features/<slice>/` with co-located hooks/api/types; break `Intel.tsx`/`Cash.tsx` into sub-components.
- Make `tsconfig` strict; fix the fallout incrementally (file-by-file `// @ts-strict` ratchet if needed).

**R5 — Ratchet quality.** Turn on stricter lint rules, raise coverage thresholds, enable mypy on more packages. Each ratchet is its own PR.

> Sequencing vs features: **R0–R2 before any new feature work** in the other docs. R3–R5 can interleave with features (e.g., the `tools/` registry refactor *is* platform-RFC P-B1; the `index.css` split *is* canvas P-A2 theming).

---

## 4. Engineering standards (the "rules", made executable)

These become a short `CONTRIBUTING.md` + a **"Conventions"** section in `CLAUDE.md` (so the AI obeys them) + lint rules where enforceable.

### 4.1 Code
- **Respect the dependency rule (§2.1).** No upward imports; no importing another module's `_private` symbols. _(import-linter + ruff)_
- **Routers are thin:** validate input (pydantic) → call one service → shape response. **No business logic / financial math in `api/`.**
- **Services own logic, are typed, and are import-pure** (no FastAPI/HTTP types). Public methods have docstrings + type hints.
- **Repositories (`db/`) do data only** — no domain rules.
- **Size budgets (lint-warn, not hard-fail):** functions ≤ ~60 lines, files ≤ ~400, cyclomatic complexity capped (ruff `C901`). The 1,154-line function is the anti-pattern.
- **Errors & logging:** typed exceptions from `core/errors.py`; structured `logging`, never `print`.
- **Money:** all monetary math through services; never trust a stored `base_amount` (already a project rule — keep it); deterministic, tested.

### 4.2 Tests (policy — what *must* exist)
- **Every service method** has a unit test. **Every API endpoint** has a TestClient smoke test (status + shape). **Every store** has a CRUD/query test (pattern exists: `test_positions_stores.py`).
- **Every bug fix ships with a regression test** that fails before the fix.
- **LLM-touching tests** use the `@pytest.mark.integration` marker (already wired to skip without a key) — unit tests never hit the network.
- **Frontend:** every non-trivial component (Testing Library) + every util (Vitest) has a test; add `@testing-library/react`.
- **Coverage:** start at a realistic floor (e.g. backend `services/` 70%), **ratchet up, never down** (CI enforces the floor).
- Naming + Arrange-Act-Assert; one behavior per test.

### 4.3 Definition of Done (a PR is done when…)
1. Code follows §4.1; **lint + type + tests + import-linter all green** locally and in CI.
2. New/changed behavior is **tested** per §4.2.
3. **Docs updated in the same PR** — `CLAUDE.md`/`FEATURES.md` and the relevant RFC/PRD (existing project rule).
4. No secrets / no personal data / no new layering violations.
5. Reviewed (human or `/code-review`; `/security-review` if it touches money, auth, or external I/O).

---

## 5. Tooling & CI gates

**Backend** — add `pyproject.toml`:
- **ruff** (lint + format; replaces black/isort/flake8) — enable `E,F,I,UP,B,C901,SIM`.
- **mypy** (start lenient: `services/`+`core/` typed; ratchet outward).
- **pytest** + `pytest-cov` (coverage floor).
- **import-linter** (the §2.1 contracts).
- Migrate to **`uv`** (or pip-tools) for a lockfile + fast installs.

**Frontend:**
- **prettier** + keep **eslint** (add `eslint-config-prettier`, react-hooks, import-order rules).
- **`tsconfig` `strict: true`** (+ `noUnusedLocals`, `noUncheckedIndexedAccess`).
- **vitest + @testing-library/react**; `tsc --noEmit` as a typecheck gate.

**Repo:**
- **`.pre-commit-config.yaml`**: ruff, prettier, gitleaks (secret scan), end-of-file/trailing-whitespace.
- **`.github/workflows/ci.yml`** (roadmap A6): backend (ruff → mypy → import-linter → pytest+cov) × Py 3.9–3.12; frontend (eslint → prettier --check → tsc → vitest → build); secret scan. **Branch protection** requires green.
- **`Makefile`**: `make setup | dev | test | lint | typecheck | fmt | check` (`check` = the full CI gate locally — one command before pushing).

---

## 6. Development lifecycle (plan → design → dev → test → review)

A documented, repeatable cycle that **maps onto skills that already exist**, so it's enforceable by both humans and Claude:

| Stage | What | Tool / artifact |
|---|---|---|
| **1. Plan** | Scope the feature, decide product trade-offs | `/feature-planning` → a **PRD** in `docs/prd/` |
| **2. Design** | Architecture, data model, contracts, risks (for non-trivial work) | `/system-architect` → an **RFC** in `docs/rfc/` or `.claude/rfcs/` |
| **3. Dev** | Implement to §4 standards, smallest steps | repo Claude skills (§7); branch off `main` |
| **4. Test** | Tests alongside code (TDD for services), run `make check` | pytest / vitest; coverage floor |
| **5. Review** | Correctness + quality + security | `/code-review`, `/security-review`, `/verify` to run the app |
| **6. Ship** | Docs updated, CI green, conventional commit, PR | DoD §4.3 |

**Right-sizing:** a typo fix skips 1–2; a new tab/tool does the lot. The point is the *default path*, written down. Add this table to `CLAUDE.md` so Claude follows it.

---

## 7. Claude-enablement (fix "not enough skills defined")

Make the conventions **executable by the AI**, not re-derived each session. Author repo-specific skills in `.claude/skills/` (each = a `SKILL.md` playbook referencing the standards above):

| Skill | Encodes |
|---|---|
| `add-tool` | new AI tool: pick domain module in `ai/tools/`, `@tool` schema + tier + handler, register, **write a test**, update `CLAUDE.md` tool list |
| `add-store` | new repository: subclass `JsonStore`, `root_key`, query methods, test (mirror `test_positions_stores.py`) |
| `add-endpoint` | thin router + service method + pydantic model + TestClient smoke test |
| `add-widget` / `add-tab` | frontend feature slice: widget registry entry, co-located hook/api/types, component test |
| `write-test` | the §4.2 policy, the `integration` marker, fixtures/seed data |
| `run-app` | project-pinned launch (ports 8080/5174, seed data) — wraps the global `run` skill |
| `new-feature` | drives the full §6 lifecycle end-to-end |

Plus:
- **`.claude/commands/`** for routine ops (`/test`, `/check`, `/seed`, `/migrate`).
- A **"Conventions / How to write code here"** section in `CLAUDE.md`: the dependency rule, size budgets, the test policy, the DoD, and a "do NOT import `api/` from `services/`, do NOT touch `tools.py`'s old if-chain — add to the registry" list.
- Run the **`fewer-permission-prompts`** skill to set a sane Bash/MCP allowlist in `.claude/settings.json` so agents aren't constantly prompted.
- Keep `.claude/settings.local.json` for personal overrides (gitignored); commit shared settings.

---

## 8. Prioritized sequencing

> _Historical — E0–E2 are done; §4/§6/§7's standards now live in `CLAUDE.md` and `.claude/skills/` rather than as a separate wave._

| Wave | Do | Why first |
|---|---|---|
| **E0 — Guardrails** | pyproject+ruff+mypy, prettier+strict tsconfig, pre-commit, CI, Makefile, one-shot auto-format, delete clutter | nothing is safe to refactor without these; cheap, days |
| **E1 — Boundaries** | `core/`+`services/`+`models/` packages; move root services; the three §2.3 fixes; import-linter → failing | unlocks the dependency rule; every later change benefits |
| **E2 — Carve backend** | `tools.py` → `ai/tools/` registry (= platform P-B1); `prompts/`; extract from ledger/intel/assistant | kills the god-files; required before tool/agent features |
| **E3 — Carve frontend** | split `index.css`; `features/` slices; strict-TS fallout; component tests | required before canvas/widget work |
| **E4 — Standards live** | `CONTRIBUTING.md` + `CLAUDE.md` conventions; author the `.claude/skills/`; coverage ratchet | makes it durable + AI-followable |

**E0–E1 should precede the feature waves** in the other docs. E2/E3 are *also* the first steps of the platform RFC (registry, theming, slices) — so the refactor and the platform build are the same work, sequenced right.

---

## 9. One-line summary

Good bones, no guardrails: a 1,154-line dispatch function, a dependency rule that's violated in both directions, and zero backend tooling. Fix order is **tooling + import-linter first**, then **mechanical boundary moves** (`core`/`services`/`models` + invert the checkpoint hook + relocate FX/cash-balance), then **carve the god-files via the registry/slice patterns already in the platform RFC**, then **write the standards, lifecycle, and repo Claude skills down so the rules are executable, not folklore.** Done right, the cleanup *is* the first phase of the feature platform — not a detour from it.
