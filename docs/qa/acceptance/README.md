# Acceptance criteria — the QA contract (GIVEN / WHEN / THEN)

This folder is the **human-readable spec of what "working" means** for each feature, and the
**script the QA agent must execute**. One file per feature/tab. The owner reads these; the
browser-use QA agent runs them.

## Why this exists

A render-only check ("the Tax tab loads, the Configure-rates button exists") gives **false
confidence** — a dead button passes it. So every criterion here is **interaction-level**:
it performs the user's action and asserts the **observable outcome**, not just element
presence. (The "Configure rates did nothing" bug existed precisely because the QA spec only
checked the button *existed*, never *clicked* it.)

## Format

Each criterion is one GIVEN / WHEN / THEN:

- **GIVEN** — the starting state / precondition.
- **WHEN** — the exact user action (click this, type that, navigate there).
- **THEN** — the observable result that proves it actually works (a value changes, a modal
  opens, a number appears — something you could screenshot).

```
### TAX-CONFIG-OPEN
- GIVEN I am on the Tax tab
- WHEN I click "Configure rates"
- THEN the "Tax rate configuration" modal opens
```

Give each criterion a stable **ID** (`<FEATURE>-<SHORT>`), so a QA report can say
`TAX-CONFIG-OPEN: PASS/FAIL` and we can track it over time.

## The QA workflow (standing rule)

When a QA agent is triggered for a feature:
1. The feature's acceptance file here is **written/updated first** (GWT, interaction-level).
2. The QA agent is **handed this file** and must exercise **every** criterion — no skipping.
3. The agent reports **PASS/FAIL per criterion ID**, with evidence (screenshot) for each FAIL,
   and classifies each FAIL as a **real app bug** vs a spec/infra issue.
4. Real bugs are fixed; the criterion is re-run to green.

The browser-use specs in `qa/browser-use/specs/*.yaml` are the **executable form** of these
criteria — each YAML spec maps to one or more criterion IDs here via its `acceptance_ids:`
field. This `.md` is the source of truth a human reads; the YAML is what the harness runs.

## Checking coverage

```bash
make qa-acceptance-check        # shows covered [+] vs uncovered [ ] per criterion
```

The tool is `qa/browser-use/acceptance_check.py` (stdlib + pyyaml; no app deps).
It exits 0 always — it is an informational report, not a gate.  A criterion is
"covered" when at least one spec's `acceptance_ids:` list includes its ID.

## How QA is EXECUTED — harness only, never computer-use

QA is run **exclusively** through the **browser-use harness** (`qa/browser-use/`), invoked via
the shell (`make qa-e2e` / `runner.py`). The harness spins up its **own isolated headless
Chromium** (Playwright) against the app booted on a high port with seeded data — it never
touches the operator's real machine.

A QA agent **MUST NOT** use the **`computer-use`** MCP (or any desktop-control tool:
`request_access`, `screenshot`, `open_application`, `left_click`, `mcp__computer-use__*`).
Those drive the operator's actual desktop and are forbidden for app testing. The agent's job is
to **invoke the harness via Bash and read its text output** — it does not drive a browser
itself. (computer-use is reserved for genuine desktop tasks the operator explicitly asks for.)

## Coverage rule

A feature is "covered" only when its acceptance file exercises **every interactive control
and every primary flow** — open it, interact, verify the outcome, plus the empty/error states.
"It renders" is not coverage.
