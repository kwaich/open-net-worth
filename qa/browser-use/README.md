# Open Net Worth — browser-use QA harness

> **This is the deterministic UI _merge gate_** (`make qa-e2e`). For the on-demand,
> per-feature **Claude computer-use** acceptance harness — a vision+action agent
> that judges "does it feel right" — see [`frontend/qa/`](../../frontend/qa/) and
> [`docs/qa/computer-use.md`](../../docs/qa/computer-use.md). `make qa` dispatches
> either mode.

An AC-driven end-to-end UI QA harness that uses
[browser-use](https://github.com/browser-use/browser-use) to test the
actually-rendered app like a real user. An LLM agent drives a headless
Chromium browser via Playwright, reading specs from plain-English YAML
files, and returns PASS/FAIL per acceptance criterion with screenshots.

## Why this exists

Unit and service tests cover logic. This harness covers:

- Features that are only verifiable in a rendered browser (drag-and-drop,
  SSE streaming, chart rendering)
- Cross-layer flows end-to-end (frontend → API → service → DB → response)
- Visual/UX regressions (empty states, error states, layout breakage)
- Lucius chat round-trips against the live assistant

## Python version isolation

The app backend runs **Python 3.9**. `browser-use` requires **Python 3.11+**.

These are incompatible at the venv level. The harness therefore lives in a
completely separate environment under `qa/browser-use/.venv/` and has ZERO
import dependencies on `backend/`. It is not listed in the app's
`requirements.txt` or `pyproject.toml`. `make qa-e2e-setup` creates and
populates this isolated venv using the **python3.12** interpreter found on
the machine (3.12 satisfies the 3.11+ requirement and is available at
`/opt/homebrew/bin/python3.12` on macOS).

If python3.12 (or 3.11) is not installed, the setup target will print an
error. Fix:

```bash
brew install python@3.12   # macOS
# or
sudo apt install python3.12 python3.12-venv  # Debian/Ubuntu
```

## LLM model

The harness uses **Claude claude-haiku-4-5** (`claude-haiku-4-5`) via the
`ANTHROPIC_API_KEY` environment variable (already required by the app).
Haiku is cost-efficient for simple DOM/screenshot tasks.

If a spec involves complex multi-step reasoning or vision tasks where Haiku
stumbles, override the model at runtime:

```bash
QA_MODEL=claude-sonnet-4-6 make qa-e2e
# or
source qa/browser-use/.venv/bin/activate
python3 runner.py --model claude-sonnet-4-6 --specs OV-01
```

Haiku is the default because the majority of these tests (navigate, read a
value, compare) are well within its capability and Sonnet costs ~5x more
per token.

## WARNING: Never QA against :5174 / npm run dev

`npm run dev` starts the Vite dev-server on **:5174**, which proxies `/api` to
`http://localhost:8080` — the owner's **REAL backend on REAL data**. The
`ONW_QA_MODE` guard is NOT active on that backend, so every write (add
transaction, update account, etc.) hits the live ledger. This is exactly how
"QA Test Coffee" leaked into the real ledger.

**Always use this harness.** `make qa-e2e` (and `boot_app.sh`) builds a
production bundle, boots the frontend on a high port (**:8092 by default**) and
the backend on another high port (**:8091**) with `ONW_QA_MODE=1` and a
secrets-stripped clone of real data (`make qa-data`). That is the only safe QA
path for the UI.

If you need to point the dev-server at a different backend for local development
(not QA), set `VITE_DEV_API_TARGET=http://localhost:8091` — but even then, use
the harness for any test that writes data.

## Quick start

```bash
# 1. Install prerequisites (once per machine)
make qa-e2e-setup

# 2. Run all specs
make qa-e2e

# 3. Run a single spec by ID
source qa/browser-use/.venv/bin/activate
python3 qa/browser-use/runner.py --specs OV-01

# 4. Run headed (visible browser) for debugging
python3 qa/browser-use/runner.py --headed --specs PL-01
```

`make qa-e2e` will:
1. Seed QA data (`make qa-data`)
2. Build the frontend (`npm run build`)
3. Boot the backend on port 8091 and frontend preview on port 8092
4. Run all specs via `runner.py`
5. Kill the servers when done

Screenshots are saved to `qa/browser-use/artifacts/`.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Anthropic API key |
| `APP_URL` | `http://localhost:8092` | Frontend URL for runner |
| `BACKEND_URL` | `http://localhost:8091` | Backend URL for boot_app.sh health checks |
| `QA_MODEL` | `claude-haiku-4-5` | browser-use LLM model |
| `QA_TIMEOUT` | `120` | Seconds per spec |
| `BACKEND_PORT` | `8091` | Backend port (must be 8090-8099) |
| `FRONTEND_PORT` | `8092` | Frontend port (must be 8090-8099) |

## Writing a new spec

Specs are YAML files in `qa/browser-use/specs/`. Copy an existing one and
fill in these fields:

```yaml
id: MY-01          # Unique ID, used in filenames and --specs filter
title: "Short human-readable title"
description: >
  Multi-line description of what this spec tests.

acceptance_criteria:
  - AC1: The thing that must be true
  - AC2: Another thing that must be true

# Task in plain English handed verbatim to the browser-use LLM agent.
# Use {app_url} as a placeholder — the runner substitutes the real URL.
browser_use_task: |
  Navigate to {app_url}.
  Do X, Y, Z.
  Return "PASS: <reason>" or "FAIL: <reason>" or "SKIP: <reason>".

expected:
  pass_condition: "result contains 'PASS'"
  screenshot_name: "MY-01-description.png"
```

**Task writing tips:**

- Be explicit about what the agent should navigate to; don't rely on it
  guessing tab names.
- Tell it what verdict tokens to return (`PASS`, `FAIL`, `SKIP`) and when.
- `SKIP` is acceptable when the spec depends on external config
  (e.g. `ANTHROPIC_API_KEY` not set for chat specs).
- Keep tasks under ~300 words; longer tasks fragment across many steps and
  increase token cost.
- For visual assertions ("the chart has a line") prefer asking the agent to
  describe what it sees and checking for keywords.

## Directory layout

```
qa/browser-use/
├── README.md           — this file
├── requirements.txt    — pinned browser-use + playwright + rich + pyyaml
├── runner.py           — loads specs, drives browser-use, prints PASS/FAIL table
├── boot_app.sh         — boots backend (port 8091) + frontend (port 8092)
├── specs/
│   ├── overview_net_worth.yaml        — OV-01: net-worth hero on Overview
│   ├── planning_dnd_reorder.yaml      — PL-01: drag-reorder category groups
│   ├── lucius_chat_round_trip.yaml    — CH-01: Lucius chat reply
│   ├── chat_scroll_on_send.yaml       — CH-02: sent message scrolled into view
│   ├── chat_post_stream_visible.yaml  — CH-03: follow-up chips/cost row visible
│   ├── chat_proposed_change_card.yaml — CH-04: proposed-change confirm card
│   ├── privacy_hide_amounts.yaml      — PRIV-01: Hide/Show amounts toggle
│   ├── connectors_tab_renders.yaml    — CONN-01: Connectors tab + detail modal
│   └── nav_tab_smoke.yaml             — NAV-01: all 6 core tabs load without crash
└── artifacts/          — screenshots + conversation JSON (gitignored)
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All specs PASS or SKIP |
| 1 | One or more specs FAIL or ERROR |
| 2 | Runner setup error (bad Python version, missing deps, no specs found) |

## How this becomes the UI merge gate

See `docs/qa/browser-use.md` for the full integration plan. The short
version: add a CI job that runs `make qa-e2e` on every PR that touches
`frontend/` or backend API routes. The job fails on exit code 1.

## Troubleshooting

### "All API calls 404" / app loads but data never populates

**Root cause:** `vite preview` does NOT inherit `server.proxy` from `vite.config.ts`.
Without an explicit `preview.proxy` block, all relative `/api/*` calls made by
the built SPA hit the preview port itself — which only serves static files and
returns 404 for any `/api/` path.

**Fix (already in place):** `frontend/vite.config.ts` now has a `preview.proxy`
block that reads `VITE_API_URL` (set by `boot_app.sh` and `make qa-e2e`) and
forwards `/api/*`, `/data.csv`, and `/actions.json` to the backend. The
`VITE_API_URL` env var must be set BEFORE running `vite preview` — `boot_app.sh`
handles this automatically.

If you run `vite preview` manually, set the var:

```bash
VITE_API_URL=http://localhost:8091 npx vite preview --port 8092
```

### "Servers die immediately after boot_app.sh exits"

**Root cause:** `boot_app.sh` registers an `EXIT` trap to clean up on failure.
If the trap fires on a _successful_ exit (after both health-checks pass), it
kills the background processes before the runner can use them.

**Fix (already in place):** `boot_app.sh` now calls `trap - EXIT` after both
servers are confirmed healthy, disarming the cleanup trap. Servers live on in
the background; the PID file at `.qa_pids` lets `runner.py` / `make qa-e2e`
tear them down cleanly at the end of the run.

### "No QA venv in a worktree"

Each git worktree shares the main repo's Python venv. `boot_app.sh` resolves
the interpreter as `${REPO_ROOT}/.venv/bin/python3` — where `REPO_ROOT` is the
repo root two directories above the script. It falls back to `python3` if the
venv is absent. You do NOT need a separate venv per worktree.

### "backend import TIMEOUT"

If the backend hangs on import, check for a stale process already bound to the
target port:

```bash
lsof -ti tcp:8091 | xargs kill -9
```

Then re-run `boot_app.sh` or `make qa-e2e`.
