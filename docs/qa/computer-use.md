# Claude computer-use acceptance QA (`frontend/qa/`)

The **on-demand, per-feature acceptance** tier of the UI testing model. A
vision+action agent **sees** the app (screenshots) and **acts** (clicks / types /
scrolls) to exercise a feature end-to-end through the real UI and judge whether it
actually *works and feels right* — the layer above the deterministic browser-use
merge gate.

> Sibling doc: [`docs/qa/browser-use.md`](browser-use.md) — the deterministic
> PASS/FAIL **merge gate**. RFC: `.claude/rfcs/phase3/computer-use-qa.md`.

## The three testing tiers

| Layer | Question it answers | Tool | When |
|---|---|---|---|
| `pytest` / `vitest` (`make test`) | Is the logic correct? | unit tests | every change / CI |
| `make qa-e2e` (browser-use) | Does the UI render and do the acceptance criteria pass, deterministically? | Playwright + YAML specs vs `docs/qa/acceptance/*.md` | **UI merge gate** |
| `make qa-cu` (computer-use) | Does the feature **work end-to-end** and **feel right** to a real user? | Claude computer-use (vision → click/type loop) | **on-demand, after a non-trivial feature** |

`make ui-gate` is a fast geometry/CSS pre-check (overflow / console-errors /
chat-overlap at 3 widths) that self-boots a disposable instance — run it while
iterating; it is not a substitute for `qa-e2e`.

## When to use which

- **browser-use (`make qa-e2e`)** — the deterministic UI **merge gate**. Run it in
  CI and before merging any frontend/API change. Maps specs to the GIVEN/WHEN/THEN
  criteria in `docs/qa/acceptance/*.md`; exits non-zero on failure.
- **computer-use (`make qa-cu`)** — the **acceptance + UX-exploration** pass. Run it
  on demand after building a non-trivial feature (onboarding, an add→edit→delete
  flow, the disposal/lots flow, an agent review queue) to confirm the interaction
  truly works and reads well. It is **non-deterministic and costs ~one API call per
  action**, so it is NOT a per-commit gate.

## Safety model — disposable instance only

A vision agent has a mouse; it could click "Reset all data". So the harness
**never touches the operator's live instance, port, or data**. On every run
(`frontend/qa/boot.mjs`) it:

- boots its **own throwaway backend** with `DATA_DIR`, `CACHE_DIR`, **and
  `LOGS_DIR`** all in a fresh `mktemp` dir, `DEMO_MODE=true` (fabricated demo
  data), `ONW_QA_MODE=true`, loopback-only, `AUTH_DISABLED=true`, with **no real
  credentials** (Anthropic/OpenAI/Tavily/Telegram/email keys stripped from the
  child env);
- **asserts before spawning** (`assertSafeBoot`): refuses port `8080`, and refuses
  unless `DATA_DIR`/`CACHE_DIR`/`LOGS_DIR` are all under the system tmp dir and
  outside the live `~/.open-net-worth/` tree;
- is double-guarded by the backend: with `ONW_QA_MODE=1`, `backend/core/paths.py`
  refuses to start if **any** of the three runtime dirs resolves into the live
  instance tree (so a future regression fails closed, never silently writes to
  production — see `backend/core/config.py`);
- **tears the backend down in a `finally`** (SIGKILL) under a hard wall-clock
  killer — no server is ever left running.

> **Port note:** the disposable instance uses port **8765** (not the `8090–8099`
> band the browser-use gate uses). That is deliberate — it is a fresh `mktemp`
> DEMO instance, not a clone of live data — but it still must never be `8080`.
> `make ui-gate`'s self-booting visual-qa uses `8093` (in-band).

## Run it

```bash
# Zero-API-cost smoke (CI-safe) — proves boot + executor + report end-to-end.
make qa-cu-mock                      # or: TASK=overview-loads make qa-cu-mock

# Live acceptance run (requires ANTHROPIC_API_KEY in env or repo .env).
make qa-cu TASK=overview-loads
make qa-cu TASK=add-transaction QA_CU_MAX_ACTIONS=25

# Override the vision model (default: claude-sonnet-4-6).
QA_CU_MODEL=claude-opus-4-8 make qa-cu TASK=overview-loads

# Direct (equivalent), with the full flag set:
cd frontend && npm run acceptance-qa -- --task overview-loads --max-actions 25

# Clean up local run artifacts afterwards:
make qa-clean
```

Output lands in `/tmp/onw-acceptance-qa/<task>-<mock|live>/`: `report.json`
(per-criterion pass/fail + bugs + usability verdict + action log), `report.txt`
(readable summary), and `step-NNN.png` (every screenshot the agent saw).

## Task specs (`frontend/qa/tasks/`)

A task is a `<name>.json` (or `.md`) acceptance spec:

```json
{
  "name": "overview-loads",
  "title": "Overview tab loads and shows net worth",
  "tab": "overview",
  "body": "Free-text context + what to exercise.",
  "criteria": [{ "id": "ov-net-worth", "text": "Shows a labeled net-worth figure." }]
}
```

`tab` pins the landing tab (the `wealthTab` localStorage key). The agent exercises
each criterion through the UI, then returns a bare JSON report keyed by criterion id.

## Model / API

The harness calls the Anthropic Messages API directly with the computer-use tool:

- tool: `{"type": "computer_20250124", "name": "computer"}`
- beta header: `anthropic-beta: computer-use-2025-01-24`
- default model: `claude-sonnet-4-6` (the current Sonnet) — override via `--model`
  or `QA_CU_MODEL`.

A Playwright executor performs the model's actions on a headless page at the same
viewport, then feeds the next screenshot back. No DOM selectors — pure
vision→action, the closest thing to a real user.

## Cost

Each executed action is one vision round-trip carrying a PNG. A 25-action run is a
handful of cents to low-tens-of-cents on Sonnet. Start with `QA_CU_MAX_ACTIONS=12`
for a smoke. Use `make qa-cu-mock` (no API call) to validate the harness itself.

This is a **dev tool** — the app never imports it; it adds no runtime dependency
beyond the existing `playwright` devDependency.
