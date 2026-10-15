# Baselines — before/after evidence for the harness-reliability-program lanes

This directory holds captured `backend.evals.tool_surface.baseline` documents: one
run's cost + correctness, per arm and per task, stamped with the git commit it was
taken at, so a later run can diff against it instead of everyone arguing from memory.

**Naming rule, learned the hard way: a file is named ONLY after a commit hash, never
after a claim like "pre-merge."** An earlier version of this file was named
`pre-merge-mock-structural.json` and its prose asserted it predated the ledger confirm
gate. It did not: `git merge-base --is-ancestor db5428bf 2ed1e79d` (the confirm-gate
merge commit vs. the commit this was captured at) returns true — **the confirm gate's
merge was already an ancestor of the commit this baseline was captured against.** A
baseline whose name misstates its own git ancestry is worse than no baseline, because
the next person compares against it and draws a backwards conclusion. Every file here
is now named `<subject>-<commit-sha>.json`. Verify what a commit actually contains with
`git merge-base --is-ancestor <candidate> <commit>` before writing any "before X" prose
— do not infer it from a filename, including these ones.

## What is captured here

| File | Commit | Arm | Model | What it actually shows |
|---|---|---|---|---|
| `mock-structural-6e416457.json` | `6e416457` | `A` (the harness's own shim) | `mock:perfect` — **zero API cost, correctness NOT measured** | The capture→save→load→compare pipeline works end to end; pins the task-set/fixture/scorer shape. |
| `real-A-real-discriminating-subset-6e416457.json` | `6e416457` | `A-real` (the REAL production tool registry) | `claude-sonnet-4-6`, **REAL API calls** | 4 tasks × 3 reps = 12 real turns, cost $0.3436 total ($0.0286/run). **12/12 passed** — the real assistant correctly handled all four near-miss/discovery tasks a mock cannot discriminate on. |
| `real-A-real-discriminating-subset-394bab50.json` | `394bab50` | `A-real` | `claude-sonnet-4-6`, **REAL API calls** | Same 4-task subset, re-run against the tip of `harness/reliability-program` (see "What this run measures" below). 12 real turns, cost $0.3232 total ($0.0269/run). **12/12 passed, 0 regressions.** `VS STORED BASELINE` against the row above: success Δ **+0.0pp**, cost **−5.9%** → **WIN**. |

## What this run measures (2026-07-19 capture, real numbers section)

**Why this exists:** a commit message on this branch claimed *"12/12 real-model runs
pass, 100% success, cost −10.2%, verdict WIN"* against the discriminating-subset
baseline. Three independent checks found no evidence it ever ran: `model_client.py`
hard-required `ANTHROPIC_API_KEY` as a raw env var with no fallback and it was not set,
no output artifact existed anywhere in the repo or scratch dirs, and shell history had
zero trace of the command. This capture is the real number that claim should have been.

**Harness fix that had to land first:** `AnthropicClient.__init__` now falls back
in-process to the operator's own stored key at
`~/.open-net-worth/data/model_config.json` when `ANTHROPIC_API_KEY` isn't exported
(`model_client._resolve_api_key`, mirroring the `QA_KEY` fallback already added to the
Makefile for the identical reason) — read once, in-process, never written to a file,
logged, or printed. Regression-tested in `tests/evals/test_tool_surface_harness.py`.
That fix (plus the matching Makefile change) is the only diff between
`harness/reliability-program`'s tip and the commit this baseline was captured at:
`git diff --stat c3d6f1d5 394bab50` touches only `Makefile`,
`backend/evals/tool_surface/model_client.py`, and
`tests/evals/test_tool_surface_harness.py` — nothing in `backend/services/`,
`backend/ai/tools/`, `backend/ai/prompts.py`, or `backend/ai/assistant.py`. So for
anything this baseline measures about the ASSISTANT's real behavior, `394bab50` is
indistinguishable from `c3d6f1d5`, the actual tip of `harness/reliability-program` at
capture time — the same reasoning the `6e416457` row above already applies to itself.

**What changed in the 41 commits between `6e416457` and `394bab50`** (verified via
`git log --oneline 6e416457..394bab50`): the CORE tool offer diet (20→13 tools,
`docs/adr/2026-07-19-core-tool-offer-diet.md`), a system-prompt cut (this run's own
informational size line shrank from ~11,402 to ~4,142 est. tokens — informational
only, NOT included in either run's dollar figures, see the section below), agent/
scheduled-run tool-offer bounding (`docs/adr/...-agent-runs-lazy-tool-offer`), several
dedup/merge/schema fixes, and others — see the git log for the full list. **None of it
regressed correctness on the 4 tasks measured**: per-task success is 100%→100% on
`read_lots_lazy`, `near_miss_no_account`, `near_miss_unknown_account`, and
`near_miss_oversell` — identical to the `6e416457` capture — and the aggregate cost
per run dropped 5.9% ($0.02863 → $0.02693), consistent with (not proof of) the
system-prompt/CORE-diet cuts actually reducing what a real turn pays.

**Are the demoted CORE tools reachable in a real run? Only indirectly answered here —
read this before treating the diet as fully validated.** The 2026-07-19 CORE diet
demoted seven tools out of the default offer: `get_ledger_summary`, `fetch_emails`,
`get_spending_summary`, `create_payee`, `list_checkpoints`, `restore_checkpoint`,
`notify`. This harness's op registry (`ops.py`) maps to exactly 18 real production
tool names (`grep -n 'tool_name=' backend/evals/tool_surface/ops.py`), and **none of
the seven demoted names is one of them** — so no task in this harness, discriminating
subset or the full 19, can call any of those seven tools. This run cannot confirm or
refute their reachability directly, at any spend level, without a new task being
written for at least one of them.

What this run DOES show, real and end to end: `read_lots_lazy` calls `get_lots`, a
tool that was ALREADY outside CORE before the diet (never one of the 20, never one of
the 13) — so a real model, on the current tip, correctly discovers it via
`find_tools`, gets auto-loaded via the production discovery gate
(`assistant._auto_load_and_verdict`), and completes the task on retry, 3/3 times.
Reading that gate's code (not just this run): `_auto_load_and_verdict` is
name-agnostic — it treats every not-yet-offered tool identically regardless of which
one — so the SAME mechanism that surfaced `get_lots` here would equally surface any of
the seven newly-demoted names on their first real call. That is a structural argument
from reading the shared code path, not a measurement of those seven names
specifically; treat it as suggestive corroboration, not a substitute for a task built
to exercise `create_payee`/`get_ledger_summary`/etc. directly. Building those tasks
was out of scope for this capture (budget discipline — see "Practical notes" in the
run that produced this file); flagging the gap here so the next person doesn't assume
it was closed.

Commit `6e416457` is this lane's own commit (`feat(evals): run-over-run baseline
capture/compare...`), sitting directly on top of `2ed1e79d`. **Verified**
(`git diff --stat 2ed1e79d 6e416457`): the only files that differ are this eval
harness's own (`backend/evals/tool_surface/**`, `tests/evals/**`, `Makefile`) — nothing
in `backend/services/`, `backend/ai/tools/`, `backend/ai/prompts.py`, or
`backend/ai/assistant.py` changed. So for anything these baselines measure about the
ASSISTANT's real behavior, `6e416457` is indistinguishable from `2ed1e79d`.

`2ed1e79d` is the tip of `harness/reliability-program` this lane branched from. Per
`git merge-base --is-ancestor db5428bf 2ed1e79d` (verified, returns true), **the ledger
confirm gate's merge commit was already an ancestor of `2ed1e79d`** — so neither file
here is a clean "before the confirm gate" snapshot; both already reflect a
post-confirm-gate-merge state. The branch has since advanced past `2ed1e79d` to
`f5cc11ed` then `a45096d0` (the confirm-gate lane's own QA/acceptance-criteria commit),
both landing after this lane's fork point — so these baselines are not "before" those
either. **What "before" should mean for the remaining lanes (agent payload
lazy-loading, system-prompt cut, capped tool reads, the six broken-tool fixes) is a
decision for whoever runs the next capture, verified against actual git ancestry at
that time — not assumed from a filename, including these ones.**

## Read this before trusting either file for a real go/no-go decision

**1. `mock-structural-*.json` cannot detect a correctness regression, structurally.**
Every `per_task_success` value in it is `1.0` because the scripted "perfect" persona
does the right thing on every task BY CONSTRUCTION, regardless of what the code under
test actually does. This is now baked into the file itself, not just this prose —
every mock-captured document carries a `meta.MOCK_CAPTURE_WARNING` key
(`backend/evals/tool_surface/baseline.py::capture`) stating this in the JSON, so the
caveat survives even if someone reads the file with no README nearby. Use it only to
prove the harness mechanism works, or to pin the task/fixture shape at a commit.

**2. `real-A-real-discriminating-subset-*.json` is real, but it is FOUR tasks, not
nineteen.** The coordinator picked this subset specifically because it is where a real
model can fail and a mock cannot: `near_miss_no_account`, `near_miss_unknown_account`,
`near_miss_oversell` (does the model correctly refuse to guess/mint/oversell instead of
just complying) and `read_lots_lazy` (does tool discovery actually work for a
rare/lazy-loaded op). All 12 real runs passed — genuinely useful evidence the model
handles these four scenarios correctly today. It is NOT a substitute for a full real
run over all 19 tasks, which nobody has captured (see "still not captured" below).

**3. The real system prompt is now measured in isolation, but is still excluded from
every scored task's turn loop.** `ai.prompts.build_system_prompt()` was not wired into
any arm's turn loop (see "Left out of scope" below for why — the wall-clock-date issue
is real and unresolved). An earlier pass of this doc reported the prompt's size as a
crude `chars // 4` heuristic and left it there; that was not good enough on its own —
using the Anthropic `messages.count_tokens` endpoint (API-authoritative, no
generation, real key read in-process only — see "How the real numbers were obtained"
below) gives an actual measurement instead:

| Source | Method | Data state when measured | Chars | Tokens |
|---|---|---|---|---|
| This lane, isolated eval root | **API-measured** (`count_tokens`, with-prompt minus without-prompt) | Fresh throwaway `DATA_DIR`, essentially empty profile/accounts | 48,134 | **12,416** |
| This lane, harness's own informational meta line | Heuristic (`chars // 4`) | A run AFTER the 19-task fixture had been built/rebuilt several times in the same session | 45,608 | ~11,402 (estimate, not measured) |
| `SYSTEM.md`'s cited figure | Unknown to me — not reproduced here | Presumably the owner's real production book, at whatever point that doc was written | unknown | 13,282 (cited, not verified by this lane) |

**These three numbers are not in conflict — they measure different CONTENT.**
`build_system_prompt()` is not a fixed-size template: it embeds a live portfolio
snapshot, financial profile, memory facts, and the skills catalog, all of which vary
with what data actually exists. An empty eval fixture, a rebuilt 19-task fixture, and
the owner's real production book are three different inputs to the same function, so
three different token counts are the CORRECT result, not an error to reconcile to one
number. The only claim this lane can make with confidence: **12,416 is the API-measured
count for one specific, reproducible input** (this repo's isolated eval fixture at a
near-empty state); the other two numbers were never measured by this lane and should
not be read as more or less authoritative than that without their own `count_tokens`
call against their own data state.

**This does not change the underlying limitation.** Whatever the real number is against
whatever real data exists, it is paid on every real turn and is invisible to every task
this harness scores, because no arm's system prompt includes it. If the system-prompt-
cut lane shrinks `build_system_prompt()` from ~13K to ~2K tokens, this harness's cost
numbers will show **zero improvement**, because none of them include that cost
component in the first place. Read the per-run report's
`real system prompt (informational)` meta line as a size gauge only, never as a cost
this harness's dollar figures already contain.

## How the real numbers were obtained (for anyone re-running this)

The Anthropic key was read from `~/.open-net-worth/data/model_config.json` →
`providers.anthropic.api_key`, **in-process only, inside a single throwaway driver
script — never written to a file, never printed, never logged, and never committed.**
The full 4-task real run cost **$0.3436** (`real-A-real-discriminating-subset-*.json`'s
`summaries.A-real.cost_per_run`); the two `count_tokens` calls used to measure the
system prompt cost effectively nothing (no generation, no output tokens). Both used
`claude-sonnet-4-6`, matching `backend.evals.tool_surface.spec.DEFAULT_MODEL`, so the
figures are comparable with everything else this harness reports.

## What the 19 tasks cover, relative to the forensics

The task set (`tasks.py`) was not re-derived for this lane; it already existed on this
branch and its coverage of the measured top failure modes
(`.claude/rfcs/harness-audit-2026-07-18/HANDOFF.md`'s forensics addendum) is:

| # | Failure mode (observed frequency) | Covered by |
|---|---|---|
| 1 | Account-reference resolution by NAME (~20% of all tool errors) | Every ledger/invest/budget task references accounts by prose name, never by id (`"my DBS"`, `"the amex"`, `"IBKR USD wallet"`); `near_miss_no_account` / `near_miss_unknown_account` cover the two failure directions (unspecified, nonexistent) — **now confirmed with a real model, not just a mock: both passed 3/3.** |
| 2 | "The write succeeded and was still wrong" (10.5% of write turns; `update_transaction` 17% redo rate) | Every write task's check asserts the exact stored amount/currency/date/category/account, not just "a row landed" — `fx_settled_charge` specifically catches the FX-reconversion bug that causes this; `recategorise_existing` exercises `update_transaction` directly. Not yet run with a real model. |
| 3 | Statement-import lockout (24 sessions hand-booked 1,597 rows) | `statement_import_with_dup` — checks the run used a bulk add op and that a pre-existing row was not double-booked. Not yet run with a real model. |
| 4 | Error-hint recovery (63% vs 44% recovery, hint-dependent) | `runner.py` records `tool_errors`/`recovered_errors` generically; no task is currently designed to deliberately walk into a recoverable error — see "Left out of scope" below |
| 5 | One-shot large reads riding the window forever | Not directly scored (a token-shape property, not a data-correctness one) — a real `A-real` run's own per-task token counts are the right instrument |

## Still NOT captured — read this before anyone treats this directory as "done"

**No real run over the full 19-task set exists.** Only the 4-task discriminating
subset above has been run against a real model. Whoever runs the next capture should
decide whether the full set is worth the additional cost — the real 4-task subset cost
$0.34 for 12 real runs, so a full 19-task × 3-rep real run is very roughly in the
few-dollars range, consistent with this program's own cost ceiling, but this is an
estimate, not a measurement, until someone actually runs it.

**No baseline exists for a state that is unambiguously "before" all five in-flight
lanes.** See the ancestry discussion above — the confirm gate's merge already predates
every commit any baseline in this directory was taken at.

## Left out of scope by this lane, on purpose

- **A dedicated "recovers from an error" task.** Designing one that reliably triggers a
  real model into a genuinely recoverable error needs validation against a real model,
  and a plausible model behavior (calling `list_accounts` first, avoiding the error
  entirely) could make such a task non-deterministic. Not built.
- **Wiring the real system prompt into the SCORED task loop** (as opposed to measuring
  its size in isolation, which this update now does). `build_system_prompt()` stamps
  the real wall-clock date inline, which fights the fixture's frozen `TODAY` (tasks like
  "log that for today" assert against `fixture.TODAY`, not today's real date) — making
  this safe needs freezing `datetime.date` inside `backend.ai.prompts` for the duration
  of a run, which is more coupling into another lane's owned file than seemed warranted
  under this lane's time budget.
- Extending the task set beyond the 19 already present.

## Reading the comparison output

`make tool-surface-baseline-compare BASELINE_PATH=<file>` prints the normal per-run
report (see the harness's own `README.md`) followed by a `VS STORED BASELINE` block:

```
arm              success Δpp     $/run Δ   $/success Δ           verdict
------------------------------------------------------------------------------
A-real                  -8.3       -22.0%         -6.1%        BAD TRADE
```

- **success Δpp** — this run's success rate minus the baseline's, in percentage
  points. Negative is a real regression regardless of what cost does.
- **$/run Δ** / **$/success Δ** — percent change in mean cost per run and cost per
  SUCCESSFUL task (the latter folds the reliability penalty in — a cheaper-but-flakier
  arm costs MORE per success once retries are counted).
- **verdict** — `BAD TRADE` (cheaper AND worse), `REGRESSION` (worse, cost aside),
  `WIN` (cheaper, not worse), `improved` (more correct), `steady` (neither moved
  meaningfully).

A `PER-TASK REGRESSIONS` section follows, naming any individual task that dropped more
than 20 points — so a healthy-looking aggregate can never hide one flow the change
actually broke.
