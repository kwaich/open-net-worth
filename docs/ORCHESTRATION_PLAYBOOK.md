# Orchestration Playbook — the "main agent" charter

A portable, project-agnostic charter for running an LLM **main agent as a multi-agent
orchestrator**. Drop this into a new repo (or paste the "Charter" section into its
`CLAUDE.md`) and the main agent inherits the working style refined here. The rules below
are the *standing instructions* the operator keeps repeating — codified once so they don't
have to be re-explained per session.

> **How to adopt in a new project:** copy this file to `docs/ORCHESTRATION_PLAYBOOK.md`,
> then add a short pointer in the repo's `CLAUDE.md`: *"This repo is run by a main
> orchestrator agent per `docs/ORCHESTRATION_PLAYBOOK.md` — follow it."* Fill in the
> **Project knobs** section at the bottom (branch name, verify gate command, model tiers).

---

## 1. Charter (the non-negotiables)

These are in force every session, without being asked:

1. **Never touch `main`.** All work happens on a long-lived integration branch (e.g.
   `auto/<phase>`). Push to the remote **for backup only** — never open/merge a PR into
   `main` unless the operator explicitly says so.
2. **≥6 lanes always.** Keep at least six agents working whenever backlog remains.
   **Refill BEFORE reviewing/merging** any finished lane — never let the running count
   drop below six while there's work to do. (Reviews and merges are themselves lanes.)
3. **Worktree isolation per lane.** Every build/fix lane runs in its own git worktree on
   its own branch, in the background. Lanes never edit the shared checkout directly.
4. **STEP-0 merge-IN, never reset.** Each lane's first action is
   `git merge <integration-branch>` into its worktree branch — pull the latest in. A lane
   **never** resets, force-pushes, or `-X ours`-merges the shared branch (that silently
   reverts other lanes' work — see §6 failure mode).
5. **Verify before merge** with the *right* reviewer (§4). A lane's own "it's done" is not
   sufficient; an independent gate is.
6. **Commit after each step; gate each merge.** Small, reviewable commits. Every merge to
   the integration branch passes the full verify gate (§5) with a HEAD guard.
7. **Sequence work that shares a hot file; parallelize everything else** (§3).
8. **Secrets & safety discipline** is never relaxed for convenience (§7).

---

## 2. Lane lifecycle

```
plan ─▶ design ─▶ build ─▶ test ─▶ review ─▶ ship
```

- **Dispatch** a lane as a background, worktree-isolated agent with a self-contained
  prompt: the task, the STEP-0 merge instruction + a BASE_OK check, the exact scope
  (which files it MAY and MAY NOT touch), the test requirement, the verify gate, and
  "commit on your worktree branch, do NOT merge — report back."
- **Refill first.** When a lane finishes, before you review it, make sure ≥6 are still
  running (dispatch the next backlog item).
- **Review** with the matching reviewer (§4). On PASS → merge with the gate (§5). On
  FAIL → dispatch a fix lane with the reviewer's findings; re-review.
- **Merge** only after the gate is green; push for backup.

---

## 3. File-contention sequencing

The one rule that prevents most merge pain: **never run two lanes in parallel that edit
the same hot file.** Typical hot files: the app shell/render switch (this repo:
`frontend/src/RouterShell.tsx`), the central nav/tab registry (this repo:
`frontend/src/tabs.ts`), a shared CSS entrypoint, a tool registry.

- Identify the hot files a task touches *before* dispatching.
- If two backlog items both touch a hot file, **sequence them** (one merges, the next
  STEP-0-merges it in, then runs). Don't parallelize them.
- **Split a feature to dodge contention:** the backend service + API + the read-only
  design spec for a feature usually touch *no* hot file, so they can run in parallel
  immediately; only the thin frontend wiring touches the hot file and gets sequenced last.
  (This is how "I don't see progress yet" gets unblocked without merge chaos.)

---

## 4. Which reviewer for which change

| Change touches… | Gate reviewer(s) |
|---|---|
| Backend logic / data / money math | **code-reviewer** (read-only, skeptical) |
| Any behavior a user exercises | **qa-engineer** (RUNS it end-to-end, not reads it) |
| **Any UI** | **ui-designer + qa-engineer** — UI is **design-first AND verify-mandatory** (BLOCKING): a wireframe/spec lane runs FIRST, a build lane implements it, then a DUAL verify — `qa-engineer` exercises every interaction on the RUNNING app, `ui-designer` (verify-build mode) confirms the rendered build matches its spec. Both must pass before merge. |
| **Significant decision made** (cross-cutting / hard-to-reverse / convention-deviating / non-obvious build choice) | record an **ADR** in `docs/adr/` — the deciding agent captures WHY (Agent provenance) |
| Money movement / auth / secrets / external I/O / code-exec | **security-reviewer** (mandatory gate) |
| Spec/scope clarity before building | product-manager / solution-architect |

UI is **design-first AND verify-mandatory** (see `docs/UI_QUALITY_GATE.md`): never
let a build lane guess placement/layout, and never let a UI lane merge on a green
build alone. One design authority produces the wireframe + build-ready spec; build
lanes implement it; then the BLOCKING dual verify runs. Required evidence to merge:
an interaction-level acceptance file (`docs/qa/acceptance/`), `make qa-e2e` green for
the touched criteria, `make ui-gate` clean, and screenshots. "It renders" is not a pass.

---

## 5. The verify gate (adapt the commands per project)

A merge is allowed only when, on the merged result:

- **Import/boot smoke** — the app imports/builds without executing a long-running server
  in the agent loop (use a timeout-guarded import, never `serve`).
- **Tests green** — the fast suite (`unit + contract`, integration/LLM tests skipped
  without keys). Report the pass count.
- **Architecture contracts** — whatever enforces the dependency rule / layering (e.g.
  import-linter, eslint boundaries) reports zero violations.
- **Types + build** — typecheck + production build clean (`tsc --noEmit` + build).
- **Frontend changes — the UI gate (BLOCKING).** For any change a user sees:
  `make qa-e2e` green for the touched `docs/qa/acceptance/` criteria + `make ui-gate`
  clean + screenshot evidence attached (`docs/UI_QUALITY_GATE.md`). A green
  `tsc`/build proves it COMPILES — never that it RENDERS or that an interaction WORKS.
- **HEAD guard** — re-check the integration branch tip hasn't moved since you computed the
  merge; if it moved, re-merge.

Never start a dev server inside an agent loop to "verify" — it hangs the lane. Use a
build/import smoke instead.

---

## 6. Known failure modes (learned the hard way)

- **`-X ours` / "resolve with ours" on a both-add file silently reverts the other side.**
  A review lane that STEP-0-merges and resolves conflicts by taking "ours" can drop a
  sibling lane's wiring (router includes, registry rows) while the *feature code* still
  exists — so the feature is present but unreachable. **Mitigation:** when two lanes both
  add to the same registry/router file, the integration step must **union** them (keep
  BOTH arms) and then **grep-verify** the wiring is present (e.g.
  `grep -c "<feature>_router" main.py` ≥ 1) before declaring merge-ready.
- **Cross-test pollution flakiness.** Some suites have order-dependent flaky tests; prove
  a failure is pre-existing (stash your change, re-run pristine base) before chasing it.
- **Optimistic-UI flicker.** A mutation that `await`s the server then refetches lets the
  UI snap back to stale state for the round-trip. Apply the optimistic local update first,
  reconcile on the refetch.

---

## 7. Secrets & safety discipline (never relaxed)

- **Money movement / trading is gated and default-OFF.** Any real-asset action goes
  through a propose→confirm flow with a single-use nonce (the model passes only the nonce;
  args are stored server-side and re-validated at apply). A kill switch defaults OFF and is
  **never env-seedable to true**. No unattended/agent/scheduled/headless path may reach it.
- **Secrets are write-only.** Credential stores: `chmod 600`, excluded from any state
  snapshot/checkpoint, **gitignored**, and covered by a regression test that asserts each
  secret file is git-ignored and untracked. `public()` returns only `has_*` flags.
- **Respect the dependency rule.** Keep the layering contracts green; no new
  ignore-exceptions.
- **Not financial/tax/legal advice.** Any estimate-style feature carries a clear
  disclaimer and takes user-supplied rates rather than hardcoding jurisdiction logic.

---

## 8. Model tiering (cost/quality)

Assign the cheapest model that does the job well; override up for hard/sensitive work.

- **opus** — architecture, security review, and genuinely hard or money-touching builds.
- **sonnet** — most building (senior/junior), test-writing, code review, UI design,
  product/spec work. The default workhorse.
- **haiku** — black-box QA / behavioral exercising, cheap high-volume checks.

If a tier is transiently overloaded (e.g. a `529`), relaunch the lane on the next tier
down when the task allows it, rather than blocking.

---

## 9. Project knobs (fill these in per repo)

- **Integration branch:** `auto/<phase>` — _set the actual name._
- **Verify gate command:** _e.g._ `make check` (lint → type → test → build → docs-lint)
  _and the import-smoke one-liner._
- **Architecture contracts:** _e.g._ import-linter (N contracts) / eslint boundaries.
- **Hot files (sequence, don't parallelize):** _e.g._ `frontend/src/App.tsx`, the tab
  registry, the shared CSS entrypoint, the tool registry.
- **Secret-store test:** _path to the gitignore-guard test._
- **Model tiers:** _confirm per-agent-type overrides._
