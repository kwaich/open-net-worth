# Tool-surface A/B harness

Measures **cost per SUCCESSFUL task** — where success means the resulting *data* is
correct, not that the turn completed.

```
cost/task = turns x tokens/turn x price/token  /  success rate
```

Reliability is a cost multiplier: a task that fails and gets retried is paid for twice.
So both numbers come out of the **same run, in the same units**, and the headline
number is `$/SUCCESS`.

## Run it

```bash
# zero-cost proof the harness + scorer work end to end (no API calls, CI-safe)
make tool-surface-eval-mock

# prove the scorer DISCRIMINATES — this must score badly
python3 -m backend.evals.tool_surface --mock --persona sloppy

# real run (needs ANTHROPIC_API_KEY; spends money)
make tool-surface-eval
make tool-surface-eval ARMS="--arm A --arm haiku=A,model=claude-haiku-4-5"
```

## Arms are parameters, not forks

An arm is a *configuration the app could ship*. Every lever is orthogonal to the task
set and the scorer, so a new comparison is a CLI flag:

| Lever | Values | The risk it carries |
|---|---|---|
| `surface` | `A` `B` `C` `A-real` | discovery cost vs. always-loaded schema cost |
| `model` | any Anthropic model id | the largest cost lever; pure reliability risk |
| `result_budget` | chars, `0` = off | model answers from a truncated set without knowing |
| `descriptions` | `full` `compressed` | terser prose loses a distinction (FX tasks discriminate) |

```bash
--arm 'A'                                  # surface A, defaults
--arm 'haiku=A,model=claude-haiku-4-5'     # model tier
--arm 'terse=A,descriptions=compressed'    # description verbosity
--arm 'small=C,result_budget=800'          # tool-result size
```

The surfaces:

- **A** — today's design: many named tools, core schemas inline, rare via `find_tools`.
- **B** — consolidated: one fat tool per domain with an `action` discriminator, no
  discovery, so a fat tool drags its cold siblings into the always-loaded set.
- **C** — describe/apply: `apply(op, items)` whose per-op shapes are *not* in the
  schema and are fetched at runtime via `describe`. Cheap schema, costs a round-trip,
  loses API-level validation.
- **A-real** — **fidelity control, not a candidate.** Runs the actual production
  registry (real `TOOL_SCHEMAS`, real `find_tools`, real `execute_tool`). If
  `A-real ≈ A` the shim is faithful and the A/B/C comparison can be trusted; if they
  diverge, discard the B/C numbers, because the baseline they are measured against is
  not the thing the decision is about.

## Why the comparison is not rigged

All arms are **projections of one op registry** (`ops.py`) and share its handlers, each
of which calls the same production service choke point the real app calls
(`ledger.create_transaction`, `planning.apply_budget_change`, `lots_service.apply_lot_change`).
Arms may reshape a call; they may not implement logic. Enforced by
`test_arms_share_the_same_handlers` and `test_every_op_is_reachable_on_every_arm`.

Every arm's catalog is padded with the same real production tool names as decoys, so
picking the right tool out of ~271 names is as hard as it is in production, and equally
hard for each arm.

## Scoring is outcome-based

Before/after snapshot of the whole book; the task's check **consumes** the rows it
expects; anything left unclaimed in the diff is an unintended side effect and fails the
run. Silence is not enough — every change must be accounted for.

Three axes, kept separate because they fail for different reasons:

- `intended` — did the correct effect land (amount, sign, currency, account, category, date, kind)?
- `unintended` — did anything else land (a duplicate, a minted account, a wrong envelope)?
- `value_ok` — for a read, does the stated figure match what the service computes? This
  **gates success** for reads (a confidently wrong number is a failed run) and is only a
  soft signal for near-misses (a clean silent refusal is already a correct outcome).

## The mock is the scorer's own test

`--persona sloppy` commits the exact bugs the tasks are designed to catch: the FX charge
booked at the re-converted amount, the absolute `assign` treated as a delta, the
duplicate statement row re-booked, an account guessed, a non-existent account
fabricated, an oversell attempted. **If `sloppy` scores well, the scorer is broken and
no real run should be trusted.** Pinned by `test_sloppy_persona_fails_every_planted_bug`.

The mock **cannot** measure the correctness axis across arms — the scripted persona
behaves identically on every arm by construction. The report therefore withholds
WIN/BAD-TRADE verdicts on any estimated run and says so loudly.

## Data safety

`bootstrap.py` sets `ONW_QA_MODE=1` and redirects `DATA_DIR`/`CACHE_DIR`/`LOGS_DIR` to a
throwaway root **before any backend import**; the backend hard-aborts if any of the
three resolves into the live `~/.open-net-worth` tree. No server is started, so no port
is used. The fixture is a deterministic seed book (see `fixture.py` for why not
`make qa-data`) rebuilt from scratch per run.

## Baseline: is a harness change actually helping?

`A/B` inside one run answers "which arm is better today." It does not answer "did
tonight's change to the real assistant move cost and correctness, and in which
direction" — that needs comparing the SAME arm across TWO runs, one taken before a
change and one after. `baseline.py` adds exactly that, on top of the same records and
the same pricing this harness already computes — see `baselines/README.md` for the
full writeup, the current commit a captured baseline was taken at, and what has and has
NOT been measured yet.

```bash
# capture a baseline from the REAL production tool registry (arm A-real, not the A/B/C
# architecture comparison) — needs ANTHROPIC_API_KEY, spends real money. OUT is
# REQUIRED and has no default — name it after the commit you captured it at (verified
# with `git merge-base --is-ancestor <sha> HEAD`), never "pre-merge": see
# baselines/README.md for why an unverified relative name is worse than no name.
make tool-surface-baseline-capture OUT=backend/evals/tool_surface/baselines/<sha>.json
make tool-surface-baseline-capture OUT=my-run.json REPEATS=5

# later — after a harness change lands — diff a fresh run against a committed baseline
make tool-surface-baseline-compare BASELINE_PATH=backend/evals/tool_surface/baselines/<sha>.json
```

The report gains a `VS STORED BASELINE` section: per arm, the success-rate delta (in
percentage points) and the cost delta (`$/run`, `$/SUCCESS`) side by side, verdicted
`WIN` / `REGRESSION` / `BAD TRADE` exactly like the in-run cost-axis table — plus any
single task that dropped more than 20 points, named individually so an aggregate can
never hide a real per-task cliff. A mock-captured side is stamped `[MOCK]` and the
report refuses to render a verdict banner as anything but a loud disclaimer: the
scripted persona's correctness is constant by construction, so a mock-vs-mock or
mock-vs-real diff proves the *mechanism* works and decides nothing else.

## Files

| File | What it is |
|---|---|
| `ops.py` | the shared op registry — every arm projects from this |
| `arms/` | the four surfaces |
| `spec.py` | `ArmSpec` + the `--arm` mini-language + description compression |
| `tasks.py` | 19 messy, realistic tasks with outcome checks |
| `scorer.py` | snapshot/diff, matchers, unintended-change detection |
| `runner.py` | the tool-calling loop, cost accounting, result truncation |
| `report.py` | per-arm summary, the cost-vs-correctness table, per-task grid |
| `baseline.py` | persist a run's summary + diff a later run against it (run-over-run, not arm-vs-arm) |
| `model_client.py` | real Anthropic client (prompt caching on) + scripted mock |
| `fixture.py` | the deterministic seed book |
| `baselines/` | captured baseline JSON files + their own README (provenance, commit, caveats) |
| `introspect.py` | arm C's `describe` (and why it does not delegate to `domain_introspect`) |
