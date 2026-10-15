# browser-use QA harness — integration guide

This document explains how the browser-use harness is used as the **UI merge
gate** for Open Net Worth, and how the QA-agent authoring workflow functions.

## What it is

`qa/browser-use/` is a completely isolated Python 3.12 environment (separate
from the app's Python 3.9 backend venv) that uses
[browser-use](https://github.com/browser-use/browser-use) to drive a real
Chromium browser via Playwright and test the app like a real user.

The QA agent reads plain-English YAML spec files, navigates the app, evaluates
acceptance criteria, takes screenshots, and reports PASS/FAIL/SKIP per spec.

## Workflow: GWT criteria drive specs

The `docs/qa/acceptance/` folder is the **source of truth**. Each `.md` file
is a feature's contract, written in GIVEN/WHEN/THEN with stable IDs.

```
docs/qa/acceptance/tax.md          # human-authored GWT spec
qa/browser-use/specs/tax_tab.yaml  # executable form — references acceptance IDs
```

Each YAML spec declares which acceptance criteria it covers:

```yaml
id: ACCT-01
acceptance_ids:
  - ACCT-LOAD
  - ACCT-SELECT
  - ACCT-ADD-OPEN
```

To see coverage at a glance:

```bash
make qa-acceptance-check
```

This produces a feature-by-feature report: which criterion IDs have at least
one spec (`[+]`), which are uncovered (`[ ]`), and an overall percentage.

**The workflow for a new feature:**

1. Author (or update) the acceptance `.md` file with GIVEN/WHEN/THEN criteria.
2. Write a YAML spec referencing those IDs in `acceptance_ids:`.
3. Run `make qa-acceptance-check` — the new criterion should move from `[ ]`
   to `[+]`.
4. Run `make qa-e2e` — the spec must PASS before merging.

**Coverage tools (QA-only, no app deps):**

- `qa/browser-use/acceptance_parser.py` — parses `.md` → `AcceptanceCriterion`
  objects; pure stdlib.
- `qa/browser-use/acceptance_check.py` — standalone coverage reporter; uses
  `pyyaml` (already in `requirements.txt`).
- `qa/browser-use/test_acceptance_parser.py` — unit tests for the parser +
  coverage logic; run with `python3 -m pytest qa/browser-use/test_acceptance_parser.py`.

## Setup

```bash
make qa-e2e-setup   # creates qa/browser-use/.venv, installs deps + chromium
export ANTHROPIC_API_KEY=sk-ant-...
make qa-e2e         # seed data, build frontend, boot servers, run all specs
```

See `qa/browser-use/README.md` for full options and troubleshooting.

## How this becomes the UI merge gate

### CI integration

Add a CI job triggered on pull requests that touch `frontend/` or
`backend/api/`:

```yaml
# .github/workflows/qa-e2e.yml (example)
jobs:
  qa-e2e:
    runs-on: ubuntu-latest
    if: "contains(github.event.pull_request.labels.*.name, 'needs-qa')"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: make qa-e2e-setup
      - run: make qa-e2e
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

The job exits 0 only when all specs PASS or SKIP. A single FAIL exits 1 and
blocks the merge.

### Port safety

The harness binds the backend on **8091** and the frontend preview on **8092**.
These are in the 8090-8099 range mandated by CLAUDE.md for test/QA servers —
they cannot collide with the dev server on 8080.

`ONW_DISABLE_TELEGRAM=1` and `AUTH_DISABLED=true` are always set. The data
directory is a fresh clone from `make qa-data` (live data stripped of secrets),
not the developer's live instance.

### When to add a spec

Add a new spec whenever:
- A new **tab or major widget** ships (OV/IN/PL/CA prefix)
- A **drag-and-drop** or other gesture-heavy interaction ships
- A **chat flow** or SSE feature ships (CH prefix)
- A **regression** is found — add the spec before the fix, confirm it fails,
  then fix it

## How the QA agent authors specs

Specs are YAML files in `qa/browser-use/specs/`. The format is documented in
`qa/browser-use/README.md`. Key design decisions:

### Plain-English tasks

The `browser_use_task` field is handed verbatim to the browser-use LLM agent.
Write it like you are briefing a junior QA engineer who has never seen the app:
navigate here → do this → look for that → report PASS or FAIL with reason.

The agent has access to vision (screenshots) and can click, type, scroll, and
wait. It cannot run code or call APIs directly — it can only interact with the
browser.

### Verdict tokens

Always instruct the agent to return exactly one of:

- `PASS: <reason>` — spec met
- `FAIL: <reason>` — spec not met (runner exits 1)
- `SKIP: <reason>` — spec skipped due to missing config (e.g. no API key)
  — treated as acceptable by the runner

The runner evaluates `pass_condition` from the YAML `expected` block by
substring match (default: looks for `PASS`).

### SKIP vs FAIL

Use `SKIP` for specs that depend on external configuration that may not be
present in all environments (e.g. `ANTHROPIC_API_KEY` for chat specs,
connector credentials). A CI environment without a key should not block the
merge due to a chat spec.

### Model choice

The harness defaults to `claude-haiku-4-5` because it is the cheapest model
that can navigate simple DOM/screenshot flows. Use
`QA_MODEL=claude-sonnet-4-6 make qa-e2e` when:

- The spec involves visual reasoning about charts or data
- The spec requires multi-step deduction from page state
- Haiku is failing intermittently on a spec that should reliably PASS

### Cost estimate

At June 2026 pricing, running 9 specs with claude-haiku-4-5 (Haiku 4.5) at
~15 steps/spec typically costs under $0.15. Sonnet 4.6 would be ~$0.75 for
the same run. CI runs on every labelled PR should remain well under $2/day.
The 4 specs that need an API key (CH-01/03/04 + any LLM spec) are skipped
cleanly when `ANTHROPIC_API_KEY` is absent, keeping CI usable without a key.

## Spec catalog

| ID | Title | Acceptance IDs covered | Needs API key |
|---|---|---|---|
| OV-01 | Net-worth hero renders non-empty | OV-HERO-STATS | No |
| PL-01 | Planning drag-reorder persists | PLAN-DRAG-REORDER-WITHIN-GROUP, PLAN-LOAD | No |
| PLAN-RET-01 | Plan Retirement sub-tab renders | PLAN-RET-LOAD, PLAN-RET-WHAT-IF | No |
| CH-01 | Lucius chat round-trip | CHAT-SEND-PLAIN, CHAT-OPEN-TOGGLE | Yes (SKIP if absent) |
| CH-02 | Chat scroll on send | CHAT-SCROLL-ON-SEND | No |
| CH-03 | Post-stream chips/cost row visible | CHAT-SCROLL-DURING-STREAM, CHAT-FOLLOWUP-CHIPS | Yes (SKIP if absent) |
| CH-04 | Proposed-change confirm card renders | CHAT-PROPOSE-CONFIRM-POSITION | Yes (SKIP if absent) |
| PRIV-01 | Hide amounts toggle masks and reveals | _(uncovered — no acceptance criterion yet)_ | No |
| CONN-01 | Connectors tab renders cards and modal | CONN-LOAD, CONN-DETAIL-OPENS | No |
| CONN-IBKR-01 | IBKR connector config renders | CONN-IBKR-CONFIG-RENDERS | No |
| NAV-01 | All sidebar tabs load without error | OV-LOAD, INV-LOAD, CONN-LOAD, SETTINGS-LOAD | No |
| INV-H-01 | Holdings cockpit — density toggle, target editor 100% gate, Sector/Region grouping | INV-H-DENSITY-TOGGLE, INV-H-DENSITY-COMPACT, INV-H-TARGET-EDITOR-OPEN, INV-H-TARGET-SUM-GATE, INV-H-TARGET-SUM-ALLOWS-100, INV-H-TARGET-DRIFT-VISIBLE, INV-H-SECTOR-TOGGLE, INV-H-REGION-TOGGLE, INV-H-SECTOR-FLAG, INV-H-FLAG-ABSENT-WHEN-BELOW-THRESHOLD, INV-H-FLAG-ICONS-RENDER, INV-H-FLAG-UNKNOWN-GRACEFUL, INV-H-EMPTY-STATE, INV-H-FILTER-EMPTY-STATE | No (SKIP if redesign absent) |
| INV-PERF-01 | Performance — risk grid conditional on data sufficiency, benchmark roster select/deselect, MWR visibility | INV-PERF-RISK-GRID-APPEARS, INV-PERF-RISK-GRID-VALUES-NON-ZERO, INV-PERF-RISK-GRID-HIDDEN-INSUFFICIENT, INV-PERF-RISK-GRID-EMPTY-STATE-NOT-BLANK, INV-PERF-BENCHMARK-PICKER-PRESENT, INV-PERF-BENCHMARK-SELECT, INV-PERF-BENCHMARK-DESELECT, INV-PERF-MWR-HIDDEN-SHORT-WINDOW, INV-PERF-MWR-VISIBLE-LONG-WINDOW, INV-PERF-NO-HISTORY-EMPTY-STATE | No (SKIP if redesign absent) |
| INV-ACT-01 | Activity — signed ledger amounts, type filter (Buy/Sell/Dividend), Tax tab deep-link | INV-ACT-LEDGER-RENDERS, INV-ACT-SIGNED-AMOUNTS, INV-ACT-AMOUNT-CURRENCY, INV-ACT-TYPE-FILTER-PRESENT, INV-ACT-TYPE-FILTER-BUY, INV-ACT-TYPE-FILTER-DIVIDEND, INV-ACT-TYPE-FILTER-ALL-RESTORES, INV-ACT-FILTER-EMPTY-STATE, INV-ACT-TAX-DEEPLINK-PRESENT, INV-ACT-TAX-DEEPLINK-NAVIGATES, INV-ACT-EMPTY-NO-TRANSACTIONS | No (SKIP if redesign absent) |
| INV-OPT-01 | Options — two sections, ≤14-day expiry chip, ITM badge | INV-OPT-TWO-SECTIONS, INV-OPT-ACTIVE-SECTION-CONTENT, INV-OPT-EXPIRING-SECTION-CONTENT, INV-OPT-14D-CHIP-VISIBLE, INV-OPT-14D-CHIP-ABSENT-BEYOND, INV-OPT-14D-CHIP-STYLE, INV-OPT-ITM-BADGE-VISIBLE, INV-OPT-OTM-NO-BADGE, INV-OPT-ITM-BADGE-STYLE, INV-OPT-EMPTY-NO-OPTIONS | No (SKIP if redesign absent) |
| INV-DOCK-01 | Lucius dock — per-tab prompt suggestions, hide while drawer open, reappear on close | INV-DOCK-SHOWS-ON-HOLDINGS, INV-DOCK-SHOWS-ON-PERFORMANCE, INV-DOCK-PROMPTS-CHANGE-ON-TAB-SWITCH, INV-DOCK-PROMPT-SENDS-TO-CHAT, INV-DOCK-HIDES-WHEN-DRAWER-OPEN, INV-DOCK-REAPPEARS-WHEN-DRAWER-CLOSED, INV-DOCK-HIDDEN-ON-OPEN-PAGE-LOAD, INV-DOCK-NO-SUGGESTIONS-GRACEFUL | No (SKIP if redesign absent) |
| INV-CT-01 | Cross-tab — Portfolio brief priority order, sparkline omitted < 2 pts, state persistence | INV-CT-BRIEF-RENDERS, INV-CT-BRIEF-PRIORITY-ORDER, INV-CT-BRIEF-TODAY-SIGNED, INV-CT-BRIEF-TODAY-ABSENT-GRACEFUL, INV-CT-SPARKLINE-SHOWN-SUFFICIENT, INV-CT-SPARKLINE-OMITTED-INSUFFICIENT, INV-CT-SPARKLINE-OMITTED-NO-HISTORY, INV-CT-GROUPBY-PERSISTS, INV-CT-DENSITY-PERSISTS-RELOAD, INV-CT-SUBTAB-PERSISTS-RELOAD | No |
| INV-ERR-01 | Investments error states — no blank pages or JS errors on any sub-tab; benchmark null-value graceful | INV-H-LOAD-ERROR, INV-PERF-LOAD-ERROR, INV-PERF-BENCHMARK-NULL-VALUES-GRACEFUL, INV-ACT-LOAD-ERROR, INV-OPT-LOAD-ERROR | No |

## Artifacts

Screenshots and conversation JSON are saved under `qa/browser-use/artifacts/`
(gitignored). They are available as CI artifacts for debugging failed specs.

## Relationship to unit tests

This harness does NOT replace unit tests or API smoke tests. It sits above them:

| Layer | Tool | What it catches |
|---|---|---|
| Service logic | pytest unit tests | Wrong math, rule violations, edge cases |
| API contracts | pytest TestClient smoke tests | Wrong status codes, shape changes |
| E2E UI | browser-use specs | Layout breakage, JS errors, UX regressions |

A spec failure means something the user would see is broken. A unit test
failure means something invisible to the UI is broken. Both must be green.
