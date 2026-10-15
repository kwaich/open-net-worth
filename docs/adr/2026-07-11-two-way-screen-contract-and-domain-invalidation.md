# ADR: Two-way screen contract + registry-tagged domain invalidation

- **Date:** 2026-07-11
- **Status:** Accepted
- **Owner decision:** approach C chosen over (A) growing the frontend MUTATING_TOOLS
  list and (B) a plan-specific staging tool — "the foundation must be solid to scale."
- **Spec:** `docs/superpowers/specs/2026-07-11-lucius-drives-plan-outlook-design.md`

## Decision

1. **Screens advertise COMMANDS** (name + compact param spec + symbolic route) the same
   way they advertise facts/prompts/handles. The manifest rides
   `ChatRequest.screen.commands`, is stashed per-turn in session meta (cleared on
   no-screen turns), and ONE generic tool — `run_screen_command` — validates and relays.
   The backend stays screen-agnostic: the frontend owns the vocabulary
   (`lucius/screenCommands.ts`), including the off-screen catalog that makes parked
   commands + navigation chips possible.
2. **Screen commands are EPHEMERAL by invariant** — UI state only, never a store write;
   a reload discards them. Durable writes remain real tools with their confirm tiers.
   The tool is interactive-only (never in `HEADLESS_ALLOWED_TOOLS`). Tier note: because
   `agent_caps._T0` is pinned equal to the headless allowlist, `run_screen_command` is
   deliberately NOT listed there — under an agent auto-apply grant the closed-world
   default treats it as T2 (refused), which is fail-safe and moot (an unattended run
   has no screen); every normal interactive/responder turn is unaffected.
3. **Freshness is deterministic, never model-driven:** every write `@tool` self-declares
   `mutates=("domain",…)`; the dispatch loop emits one deduped `data_changed` SSE before
   `turn_done`; `useDataVersion(domain)` is the whole per-tab adoption story. The
   frontend's hand-maintained `MUTATING_TOOLS` set is deleted (enforcement +
   old-coverage tests pin the migration: `tests/test_tool_domains.py`).

## Why

- A "please refresh" screen command would put data freshness in the model's hands;
  registry tags keep it structural.
- One tool + a client-owned manifest scales to every future screen without new backend
  surface; parked/sticky bus semantics make the off-screen case (chip → navigate →
  apply) correct even after a reload, because the chip re-dispatches from the persisted
  tool pair rather than depending on the original SSE.
- Two Monte-Carlo engines (backend `simulate_retirement`, client `planSim.worker`) can
  differ by a few points — accepted v1 looseness; prompt guidance says "≈" and defers to
  the chart. Unification is out of scope.

## Agent provenance

Designed and implemented end-to-end by Claude (Opus 4.8) with the owner selecting
approach C and the intent-routed semantics (what-if → ephemeral staging; stated fact /
"update my plan" → persisted write) in the brainstorming session of 2026-07-11.
