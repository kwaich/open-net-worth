# Computer-use acceptance-QA harness (`frontend/qa/`)

> **Entry points:** `make qa-cu` (live) · `make qa-cu-mock` (zero-cost smoke) ·
> `make qa MODE=computer-use`. Default vision model is now `claude-sonnet-4-6`
> (override with `--model` / `QA_CU_MODEL`). The deterministic UI **merge gate** is
> the sibling [browser-use harness](../../qa/browser-use/) (`make qa-e2e`). Usage +
> when-to-use-which: [`docs/qa/computer-use.md`](../../docs/qa/computer-use.md).

Tier-2 of the testing model (RFC `computer-use-qa`, slice **S1**).
An agent that **sees** the app (screenshots) and **acts** (clicks/types) to verify
a feature end-to-end through the real UI and judge its UX — the layer above
deterministic Playwright `visual-qa` (geometry) and pytest/vitest (logic).

| Layer | Question | Tool |
|---|---|---|
| pytest / vitest | Is the logic correct? | unit tests |
| `npm run visual-qa` | Does it overflow / overlap / error at 3 widths? | Playwright (geometry) |
| **`npm run acceptance-qa`** | Does the feature **work end-to-end** and **feel right**? | computer use (this) |

This is **computer use** — Claude receives a screenshot, reasons, and emits a
`computer` tool action (click x,y / type / scroll / key); a Playwright executor
performs it on a headless page at the same viewport, then feeds the next
screenshot back. No DOM selectors. Actuated by Playwright (no desktop VM).

## ⚠️ Safety model — disposable instance ONLY

A vision agent has a mouse; it can click "Reset all data". So the harness **never
touches the operator's live instance or data**. On every run it:

- boots its **own throwaway backend** with `DATA_DIR` + `CACHE_DIR` in a fresh
  `mktemp` dir, `DEMO_MODE=true` (demo-seeded fabricated data), on a **dedicated
  port** (`8765`, never `8080`), serving the built `frontend/dist`, loopback-only,
  `AUTH_DISABLED=true`, with **no real credentials** (Anthropic/OpenAI/Tavily/
  Telegram/email keys are stripped from the child env).
- **asserts before spawning** (`boot.mjs` `assertSafeBoot`): refuses to run if the
  port is `8080`, or if `DATA_DIR`/`CACHE_DIR` is not under the system tmp dir, or
  if either path contains the live `/.open-net-worth/` segment.
- **tears the backend down in a `finally`** (SIGKILL the child) and runs under a
  **hard wall-clock killer** that always kills the child and exits — no server is
  ever left running.

The agent is system-prompted not to click destructive buttons (Reset / Clear
demo / Delete) unless a criterion requires it — but even if it did, it only ever
hits throwaway demo data.

## Caps (hang + cost safety)

- `--max-actions` (default **25**) — stop after N executed actions.
- `--max-seconds` (default **120**) — soft loop cap; a separate hard killer fires
  at `max-seconds + 30s` and force-exits.
- Each action is one Anthropic API round-trip with an image. Bounded by design.
  Every action is logged (and in the report's `actions_log`).

## Run it

```bash
cd frontend

# Mock — NO API call, zero token cost, CI-safe. Proves boot + executor + report.
npm run acceptance-qa -- --mock

# Live — requires ANTHROPIC_API_KEY in the environment (or repo .env).
npm run acceptance-qa -- --task overview-loads
npm run acceptance-qa -- --task add-transaction --max-actions 25 --max-seconds 120

# Flags:
#   --task <name>        task spec under frontend/qa/tasks/ (default overview-loads)
#   --mock               deterministic API-free canned flow
#   --max-actions <n>    action budget (default 25)
#   --max-seconds <n>    soft time cap (default 120; hard killer at +30s)
#   --out-dir <dir>      report + screenshots root (default /tmp/onw-acceptance-qa)
#   --port <n>           disposable backend port (default 8765; 8080 refused)
#   --model <id>         vision model (default claude-sonnet-4-6; or env QA_CU_MODEL)
```

Output lands in `<out-dir>/<task>-<mock|live>/`:
- `report.json` — structured per-criterion pass/fail, bugs, usability verdict, action log.
- `report.txt` — readable summary (also printed to stdout).
- `step-NNN.png` — every screenshot the agent saw, in order.

Exit code: `2` if the run's overall verdict is `fail`, `1` on a harness error,
`0` otherwise (read the report for `partial`/`unknown`).

## Task specs (`tasks/`)

A task is a `<name>.json` (or `.md`) acceptance spec. JSON shape:

```json
{
  "name": "overview-loads",
  "title": "Overview tab loads and shows net worth",
  "tab": "overview",
  "body": "Free-text context + what to exercise.",
  "criteria": [{ "id": "ov-net-worth", "text": "Shows a labeled net-worth figure." }]
}
```

`tab` pins the landing tab (`wealthTab` localStorage key — `overview`,
`transactions`, `investments`, `cash`, …). The agent is told to exercise each
criterion, then reply with a bare JSON report keyed by criterion id.

## What the OPERATOR must do to use it for real

1. **Provide an `ANTHROPIC_API_KEY`** (env or repo `.env`). Live runs cost tokens:
   roughly one vision round-trip per action (≤ `--max-actions`), each carrying a
   PNG — a 25-action run is a handful of cents to low tens of cents on Sonnet.
   Start with `--max-actions 12` for a smoke.
2. **Add task specs** under `frontend/qa/tasks/` for the flows you care about
   (onboarding, add→edit→delete transaction, the disposal/lots flow, the Agents
   review queue) — one JSON per flow, listing concrete acceptance criteria.
3. **Run on-demand after a non-trivial feature** (Tier-2 acceptance pass), not per
   commit — it is non-deterministic and slow vs. the Playwright Tier-1 net.
4. Read `report.txt` + the `step-*.png` screenshots for the verdict and any bugs.

This is a **dev tool** — the app never imports it; it adds no runtime dependency
beyond the existing `playwright` devDependency.
