---
title: One retirement engine, on the server; a monthly saving is a stored fact
status: Accepted
date: 2026-10-14
deciders: senior-engineer lane (Claude) + repo owner
---

# One retirement engine, on the server; a monthly saving is a stored fact

Amends `2026-09-03-outlook-whatif-staging-hardening` (its premise that the simulation runs in the
browser) and supersedes the "two engines" note in `2026-07-11-two-way-screen-contract-and-domain-invalidation`.

## Context

Persona QA (2026-10-13/14, a 44-year-old Singapore teacher asking "am I on track to retire at 62,
how much more should I save?") got different answers to the SAME question from the Plan › Outlook
screen and from Lucius: screen 57% then 73%, Lucius 52%, 94% and 81%. Two independent causes:

1. **Two engines.** The Outlook ran its own Monte Carlo in the browser (`planData.ts`
   `simulateLife` / `simulateOutcomes`, in a worker), with its own defaults, its own RNG and its own
   reading of "the plan". `simulate_retirement` ran `services/montecarlo.py` — a different model
   (nominal returns, year-offset streams, no shocks, no age-based timing), different defaults
   (7% return, 15% volatility, income − expenses) and a different seed. `53d9a4446` only handed the
   screen's inputs to Lucius and told him to quote the screen; he still ran his own simulation the
   moment the user asked a what-if, and could not reproduce the screen's number.
2. **Saving had no field.** A monthly saving was DERIVED as `monthly_net_income − monthly_expenses`,
   and every writer that held a saving (the Outlook form, the setup dialog, "Update my FIRE plan")
   stored it as `monthly_net_income = saving + expenses`. "I save 1,200" with expenses unknown
   stored an INCOME of 1,200 (Lucius then called it a placeholder); later giving the real income
   6,100 and spending 4,000 silently turned the plan into a saving of 2,100.

Balances and money math belong in `services/` (CLAUDE.md); a model that is also a number the user
reads must have one definition.

## Decision

### 1. The simulation is `services/montecarlo.py`, and nothing else

- `services/montecarlo.py` is the ONE engine: monthly steps over the whole life in today's money,
  saving until the retire age then drawing monthly spending minus guaranteed income, one-off events
  (ledger-signed), market-shock windows by age (weak returns, lost decade, inflation, a crash),
  ruin the first time a path goes below zero. All randomness is one `default_rng(FAN_SEED)` laid
  out (path, month): identical inputs give byte-identical output, and a smaller `n_paths` is exactly
  the first rows of a larger one, so the quick (dragging) fan, the full fan and every scan read the
  same paths. The insight scans (safe-to-retire age, "+X/mo lifts this to 90%", the age the typical
  path reaches the FIRE number) vectorise their candidates over the same draws.
- `services/retirement_plan.py` decides what goes in. The **saved plan** is the profile's FIRE
  settings (`derive_fire_inputs`, so a stated saving wins) overlaid with the Outlook baseline
  (levers, guaranteed-income streams, life events). A caller passes only the levers it CHANGES;
  everything else is the saved plan, and the response names every lever it moved
  (`changed`). Net worth is read server-side, never trusted from a caller.
- **Screen:** `POST /api/plan/outlook` (the fan, `quick` while dragging, Rich/Broke/Dead on
  request), `POST /api/plan/outlook/insights`, `GET /api/plan/scenario-odds` (every saved
  scenario through the same engine — a recalled scenario's shelf badge IS its headline). The browser
  keeps only presentation; `simulateLife`, `simulateOutcomes`, the worker and its scan helpers are
  deleted.
- **Lucius:** `simulate_retirement` takes the Outlook's own lever vocabulary (the keys
  `set_plan_baseline` / `create_plan_scenario` already use), defaults every one to the saved plan,
  returns `changed_from_saved_plan` with each lever's saved value, and with no arguments returns
  exactly the screen's numbers (same inputs, same engine, same paths).
- `GET/POST /api/montecarlo` (unused) and the old year-offset service are removed; nothing
  re-implements the model.

### 2. A monthly saving is its own stored fact

`monthly_saving` is a profile field. The Outlook form, the setup dialog and "Update my FIRE plan"
write it and never touch income; income and spending edits (REST, `update_profile`, the Cash Flow)
never touch it. `services.fire.monthly_saving()` is the one reader: a stated saving wins, otherwise
`income − expenses` exactly as before. Nothing is migrated — a missing field means "derive it".

The saved plan is the profile overlaid with the Outlook baseline, so a baseline lever from an
earlier "Update my FIRE plan" would hide a figure the user states afterwards — with the Outlook
closed, nothing re-syncs it. The profile write choke (`services.profile.write_profile`) therefore
hands an edited setting back: a baseline lever whose profile counterpart (saving, return,
volatility, retirement spending, withdrawal rate, life expectancy, retirement age) just changed to
a DIFFERENT value is dropped. A lever that already agrees (the Outlook's own save writes both) and
every unrelated write leave the baseline untouched.

### 3. CPF realism: only what has a clean signal

- A CPF LIFE payout is a guaranteed-income stream (`{label, amount, age}` with no `endAge`), which
  the engine only ever applies against the drawdown; the tool description shows the shape.
- **MediSave (and any other earmarked, non-spendable balance) is NOT excluded from the pot.** The
  only signal is `account.type == retirement`, which also covers CPF OA/SA, SRS and pensions that
  are legitimately part of the pot; there is no sub-type, tag or convention for MediSave, and
  guessing from an account's free-text name is exactly what this app refuses to do. The gap is
  recorded here and in the product docs rather than papered over (see Follow-ups).

## Consequences

### Positive
- One number for one question, on the screen, in chat, in Telegram and in a headless run — the
  headline, the shelf badges and Lucius cannot drift because there is nothing to drift between.
- "The same inputs" is checkable: the response carries the resolved inputs and the levers moved.
- The engine is tested in pytest (determinism, path-prefix property, every modelled effect); the
  browser tests cover only the wire mapping and the request coalescing.
- A stated saving survives any later income or spending edit.

### Negative
- The Outlook now needs the server for every lever change (one ~60–100 ms request while dragging,
  ~0.2 s for the settled fan and the insights); there is no offline fan. Dragging coalesces to the
  latest request and uses 1,400 paths.
- The headline numbers moved: the new engine fixes the old one's small inconsistencies (one random
  stream for every scan, the scans' 1,000 paths instead of 280, a net-worth change re-running the
  fan, and the Rich/Broke/Dead split now drawn from the fan's own paths).
- Headline odds may shift by about a point against what the old browser engine showed for the
  same inputs: percentiles are interpolated rather than floor-indexed, the seeds and path counts
  differ (one seed; scans on 1,000 paths, not 280), and the horizon is capped at 100 years.
  NumPy does not promise a Generator's stream across releases, so an upgrade can do the same;
  determinism is "same inputs, same install", not forever.
- The shock-window defaults exist twice: the server resolves them for the simulation, the screen
  re-derives them only to annotate the chart and seed the shock editor.

### Follow-ups
- A MediSave signal: an account sub-type or tag (and its UI) so earmarked balances can be excluded;
  until then a user who holds MediSave in a `retirement` account should lower the starting pot
  with the `startNW` what-if or keep it in a separate record.
- `plan.stage_whatif` can now report the engine's number for what it stages (the 2026-09-03 ADR
  could not, with the simulation in the browser).
- Expose the resolved shock windows from the API and drop the screen's copy of the defaults.
- Income/spending edits on a profile with NO stated saving still leave an older baseline `saving`
  in force (the derived value moves, but it is not a mirrored setting); a user who never states a
  saving and saves the Outlook once keeps that figure until they edit it.

## Alternatives considered
- **Keep the browser engine and have Lucius read the screen's result** — rejected: Lucius runs
  headless (Telegram, Automations, MCP) where no screen exists, and a what-if still needs a number;
  this was `53d9a4446` and it failed the first time a user asked one.
- **Port the JS engine to Python and keep both in step with a parity test** — rejected: two
  implementations in two runtimes is the bug; a parity test only reports it.
- **Run the one engine in the browser (WASM/JS) and ship it to the server** — rejected: the server
  owns the profile, the baseline and net worth, so it is where "the saved plan" is resolved.
- **Keep deriving the saving, but write it as income more carefully** — rejected: income is a
  separate fact the budget lens, Cash Flow and the system prompt read; a saving stored as income
  corrupts all of them.
- **Backfill `monthly_saving` for every existing profile** — rejected: it would freeze a derived
  number as a "stated" one and re-create the bug in reverse.

## Agent provenance

- **Decided by:** senior-engineer lane (Claude), at the orchestrating agent's request
- **Lane / branch:** `worktree-agent-aefb0380452b69d72`
- **Alternatives weighed:** screen-fed Lucius vs a parity-tested pair vs one server engine; derived
  vs stored saving (above)
- **Driving artifacts:** persona QA 2026-10-13/14 (Singapore teacher), commit `53d9a4446`
- **Why this way (one line):** a number the user reads and a number Lucius says must come from one
  function on the one machine that knows the saved plan.
