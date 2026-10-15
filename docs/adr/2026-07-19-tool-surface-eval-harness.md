---
title: Tool-surface eval harness — shim every arm over one op registry, score the data, report cost per SUCCESSFUL task
status: Accepted
date: 2026-07-19
deciders: harness/reliability-program lane (Claude) + owner directive (owner AFK dispatch)
---

# Tool-surface eval harness — shim every arm over one op registry, score the data, report cost per SUCCESSFUL task

## Context

Two standing questions about the AI tool surface were being argued from a token model
rather than measured:

1. **Which tool surface does the model perform better against?** The app exposes 271
   named tools (~19 always-loaded, the rest lazy behind a names-only catalog +
   `find_tools`). Two alternatives were on the table — consolidation into fewer, fatter
   tools, and a thin `apply(op, items)` dispatcher whose per-op shapes are fetched at
   runtime via `describe` — and nobody knew which was better.
2. **What does a correct task actually cost?** Cheaper models, smaller tool results, and
   compressed field descriptions are all live cost levers, and all three fail the same
   silent way: the model still answers, the turn still completes, and the *data* is
   quietly wrong. A cost saving that buys a reliability regression is not a saving.

The decision needed evidence, but refactoring the app three ways to get it was not
affordable.

## Decision

### 1. Arms are thin shims over ONE shared op registry — not app refactors

`backend/evals/tool_surface/ops.py` is a single table of ~18 ops, each calling the same
production service choke point the real app calls (`ledger.create_transaction`,
`planning.apply_budget_change`, `lots_service.apply_lot_change`, ...). Every arm is a
*projection* of that table:

* **A** — one named tool per op, core inline, rare behind discovery (today's design).
* **B** — one fat tool per domain with an `action` discriminator, no discovery.
* **C** — `describe(path)` + `apply(op, items)` with opaque items.

They share the handlers, so they cannot diverge in business logic; only the shape of the
surface differs. Pinned by `test_arms_share_the_same_handlers` and
`test_every_op_is_reachable_on_every_arm`. **No existing tool, service, or registry was
modified.**

### 2. A fidelity control, because a shim invites the strawman objection

`A-real` runs the ACTUAL production registry (real `TOOL_SCHEMAS`, real `find_tools`,
real `execute_tool`). If `A-real ≈ A` the shim is faithful and the comparison stands; if
they diverge, the B/C numbers must be discarded because the baseline they are measured
against is not the thing the decision is about. It is excluded from the default arm set
because it does not share `ops.py` handlers — it is a calibration, not a candidate.

### 3. Scoring asserts on the resulting DATA, and unclaimed changes fail the run

Before/after snapshot of the whole book; a task's check **consumes** the rows it expects;
anything left in the diff that no expectation claimed is an unintended side effect and
fails the run. The inversion is the point — a checker that only asserts "the right row is
present" silently passes a run that also booked it twice.

Read tasks additionally gate on the stated figure matching what the service computes: a
confidently wrong number is a failed run, not a footnote. Near-miss tasks do not gate on
it, because a clean silent refusal is already a correct data outcome.

### 4. An arm is a PARAMETER, not a fork

`ArmSpec{surface, model, result_budget, descriptions}` with a `--arm` mini-language, so
model tier, tool-result truncation and description verbosity are all A/B-able against the
same task set and the same scorer without touching either.

### 5. The headline number is cost per SUCCESSFUL task

`$/SUCCESS = mean $/run / success rate`. Reliability is a cost multiplier — a failed task
is paid for once to fail and again to retry — so folding it in is what makes a
cheaper-and-worse arm read as WORSE. The report flags any arm that is cheaper AND less
correct as a `BAD TRADE`. Costs are priced through `backend/ai/models/pricing.py` rather
than a local rate table, so a discount-constant fix elsewhere corrects these numbers on
the next run.

Prompt caching is enabled on the real client (cache_control on the last tool + the system
block, mirroring `anthropic_provider`), because without it the cache-read/cache-write
split — which is where the money actually moves — would be structurally zero.

### 6. A deterministic seed fixture, NOT `make qa-data`

A live clone is right for UI QA and wrong here: the scorer needs exact ground truth,
cross-arm comparison needs an identical starting state, and variance measurement needs the
fixture to contribute zero variance. Realism comes from fixture *design* (messy account
names, multi-currency, a custodian securities account, a pre-existing row a later import
task duplicates, open lots with known basis).

### 7. Arm C's `describe` does NOT delegate to `services.domain_introspect`

The parallel lane's primitive landed mid-build, but it introspects the **live service
write chokes** — a different op namespace than arm C's `apply` executes. Wiring them
together would hand the model op names it cannot call, which is a harness bug rather than
a property of the describe/apply surface. Arm C therefore measures the describe/apply
*shape*, not that implementation's output quality, and the run header says so.

## Consequences

* The architecture decision can be made on measured success rate and measured cost.
* Any future cost lever gets tested the same way for the price of a CLI flag.
* `make tool-surface-eval-mock` is a zero-cost CI-safe proof; the LLM-calling test is
  `@pytest.mark.integration` so CI never invokes it.
* **The mock cannot measure the correctness axis** — the scripted persona behaves
  identically on every arm by construction — so the report withholds WIN/BAD-TRADE
  verdicts on estimated runs. Only a real run decides anything.

## Agent provenance

Built by the `harness/reliability-program` lane. An adversarial `code-reviewer` pass was
run specifically against the anti-rigging claims and found four real biases, all fixed
before landing and each now pinned by a regression test:

* arm A's shim **refused** a bare call to an unloaded tool, where production auto-loads and
  bounces — a handicap landing exactly on the discovery tasks;
* `descriptions=compressed` deleted arm B's `Requires:` lines, which are B's ONLY copy of
  its per-action required fields (A and C carry theirs in JSON Schema / `describe`) — the
  lever now compresses prose line-aware and preserves the contract;
* arm B pre-validated required fields in its own dispatch, handing it the friendliest
  error for the very mistake its flattened schema makes most likely — moved to the shared
  layer;
* the mock billed every post-first call as a cheap cache read even when the tool set had
  grown, understating arm A's cost on discovery tasks.

The `sloppy` mock persona exists to test the SCORER, not the model: it commits each bug
the tasks are designed to catch. It also earned its keep during the build by exposing a
real scorer bug — a read task reporting a confidently wrong number was scoring as a pass.
