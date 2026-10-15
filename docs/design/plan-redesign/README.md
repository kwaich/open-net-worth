# Handoff: Plan tab — Projections & Retirement redesign — Open Net Worth

> REFERENCE ONLY — do NOT ship the files in this folder. They are self-contained
> React-via-Babel prototypes (visual + behavioral source of truth). Recreate the
> design in the real frontend (`frontend/src/components/...`) using existing kit
> primitives and `plan-*`/`fire-*` CSS. Lift exact values from this README and the
> prototypes.

## Overview
Redesign of the **Projections** and **Retirement** sub-tabs of the **Plan** tab (`Planning.tsx`). Goal: make two analytics-heavy screens *understandable*. Both keep their real two-panel shape (a deterministic top panel + a Monte-Carlo bottom panel sharing one engine), but reframe the jargon in plain language, add (i) tooltips instead of paragraphs, keep the recognizable term "Monte Carlo", let users **save scenarios**, and **clearly separate the two tabs** so the shared simulation no longer feels duplicated.

## Fidelity
**High-fidelity.** Real Open Net Worth tokens already in use (Plus Jakarta Sans, `--ibkr` blue accent, `--good`/`--bad`/`--warn`, surfaces/lines). Match closely; prefer an existing token over a literal.

## The mental model this encodes (important)
Both tabs share the **same Monte-Carlo engine** (`montecarlo.py`, 2,000 paths × 40 yrs). The real split is **per-panel, not per-tab**:

| | Top panel (deterministic) | Bottom panel (stochastic) |
|---|---|---|
| **Projections** | Forecast accuracy — did past forecasts match reality | Monte Carlo **sandbox** — generic what-ifs, withdrawal phase **off** |
| **Retirement** | FIRE trajectory — flat-return line to your FIRE number | Monte Carlo **readiness** — withdrawal phase **on**, income streams, verdict |

- **Projections answers** "are my forecasts any good, and what could markets do to me?" (open-ended, tinkering, affordability what-ifs).
- **Retirement answers** "can I retire, by when, and will my money survive the withdrawal phase?" (goal-locked, verdict).
- They are **not** duplicates: same engine, different defaults + framing. The redesign earns the duplication by dropping retirement framing from Projections and adding a clear "Open Retirement →" pointer.

## Component mapping (where each lives today)

| Redesign element | Real component / endpoint |
|---|---|
| Plan sub-tab nav | `Planning.tsx` `PageTabs` (Budget/Projections/Retirement) |
| Projections › Forecast accuracy | `ForecastScorecardPanel.tsx` ← `GET /api/forecast-scorecard` (`forecast_scorecard.py`) |
| Projections › Monte Carlo sandbox | `ScenarioPanel.tsx` ← `GET /api/montecarlo` (query params) |
| Retirement › FIRE trajectory + settings | `FireSection.tsx` ← `GET /api/fire` (`fire.py`) |
| Retirement › readiness Monte Carlo | `RetirementReadiness.tsx` ← `POST /api/montecarlo` (body w/ income streams) |

## Key decisions baked into the redesign
1. **Plain language + (i) tooltips.** Paragraphs removed; a click-to-open info popover explains *Monte Carlo, p10/p50/p90, money-lasts, MAPE, withdrawal rate*. The scary raw stats (MAPE, bias, drift) sit behind a "Show the technical numbers" toggle.
2. **Keep the term "Monte Carlo"** in panel titles (it's well recognized) — *"Monte Carlo — what could markets do to your money?"* / *"Monte Carlo — will it survive a real market?"* (titles aligned across both tabs, Monte-Carlo-first).
3. **Forecast accuracy reframed honestly:** split **cash-flow accuracy** (controllable, ~within 18%, reliable) from **net-worth drift** (market-driven, inherently noisy) — so the broken-looking MAPE isn't the headline.
4. **Save scenarios.** Projections' preset bar unifies built-in presets + a user-saved scenario (✦) + **"+ Save current"**. Retirement has **A/B Compare saved plans** (e.g. Current vs Retire @45).
5. **FIRE settings moved from Profile → Retirement tab.** The persistent inputs (annual spend, **safe withdrawal rate**, expected return, volatility, target age, monthly saving) are now edited *here* with **Save**, showing the live derivation *FIRE number = annual spend ÷ withdrawal rate*. The Monte-Carlo section's inputs became **temporary "quick what-ifs"** (preview-only, don't change the saved plan). → **Backend note:** `GET /api/fire` + `/api/montecarlo` should read these from the saved Retirement settings; Profile should mirror or defer.
6. **Income streams are user-defined, NOT hardcoded.** "CPF LIFE" was Singapore-specific and is removed — income streams are generic editable rows: **monthly amount, start age, inflation-linked?**. (Optional future: per-country estimators — CPF/SSA/UK — that feed the same slot.) Currency stays the user's base currency (per-account, not an SG assumption).
7. **Verdict-led Retirement, flat surfaces.** No gradients. The hero states the verdict (At risk · 41% money lasts · projected age 39.5 vs target 35 · gap +S$/mo).
8. **"FIRE reached ≠ safe" is intentional.** FIRE (deterministic, flat 7%, 4%-forever) can show "on paper you arrive at 39.5" while the median Monte-Carlo path drains by ~age 62 — because the 4% rule is calibrated for ~30 yrs and early retirement stretches it. Consider surfacing this as a one-line bridge under the verdict.

## Projections screen (top → bottom)
- **Hero** (flat card): pill "Forecasts on point" + question line, then 3 at-a-glance reads — Cash-flow forecast *Reliable*, Net-worth forecast *Noisy*, Markets · 40-yr Monte Carlo *41% lasts* (with (i)).
- **Panel 1 — "How close have our forecasts been?"** window chips (1M/3M/6M/1Y); two reads (cash-flow reliable / net-worth market-driven) as left-accent sub-cards; the per-month plan-vs-actual table (cash-flow miss / net-worth miss badges); "Show the technical numbers" toggle (MAPE/avg miss/bias/drift).
- **Panel 2 — "Monte Carlo — what could markets do to your money?"** scenario+save bar (Base / Stress / Big purchase / ✦saved / + Save current); inputs (expected return, volatility, monthly saving); 4 stat cards (Typical p50 / If unlucky p10 / If lucky p90 / Money lasts) each with (i); the 40-yr fan chart (median line + p10–p90 band + "runs dry ~age N"); legend; "Open Retirement →" pointer; Lucius hint.

## Retirement screen (top → bottom)
- **Verdict hero** (flat card): pill "At risk" + question; big **41.35%** "chance your money lasts" (with (i)); HeroStats — Projected retirement *age 39.5 / target 35*; To hit target *+S$10,355/mo*.
- **Panel — "Your path to FIRE"** stat sub-cards (FIRE number, Progress bar, Years to FI); the deterministic trajectory chart (net-worth line + dashed FIRE-target line + crossing marker); then **FIRE settings** (moved from Profile): derived FIRE-number line + editable fields (annual spend, safe withdrawal rate (i), expected return, volatility, target age, monthly saving) + **Save / Reset**.
- **Panel — "Monte Carlo — will it survive a real market?"** (i) + paths · withdrawal phase on; **income streams** (generic, editable, + Add income); 3 stat cards (Typical / If lucky / Money lasts); the readiness fan chart (+ "money runs out ~age 62"); legend; **Quick what-ifs** chips (preview only — Retire 5 yrs earlier / Bad sequence / Save +S$1k/mo / 2008 crash); **Compare saved plans** A/B (Current *At risk 41%* vs Retire @45 *On track 78%*, winner highlighted); Lucius hint.

## Shared components (reusable primitives)
- **Info** — 16px circled "i", click-to-open popover (surface-2, line-strong border, shadow). Use for term/number explanations.
- **Pill** — tone pos/warn/bad/info, dot + label.
- **Fan chart** — SVG: p10–p90 band (teal 10%), p50 line (teal 2.5px), dashed "runs dry" marker, compact `S$k/M` y-ticks, age x-ticks every 8.
- **Trajectory chart** — single line + area + dashed target line + crossing dot.
- **Stat sub-card** — label (+optional (i)) + 21px/600 value, tone-colored.
- **Scenario/save bar**, **window chips**, **month table + miss badges**, **tech toggle**.

## Design tokens (from `frontend/src/index.css`)
```
--bg #0e1014  --bg-2 #15181f  --surface #171a21  --surface-2 #1e222c  --line #272c38  --line-strong #313749
--ink #f3f5f9  --muted #9aa3b2  --faint #646c7c  (zeros/sub ~#4a515f)
--good/--liquid #34b08a (teal, positive/median) · --bad #e2554f (red, negative/risk) · --warn #e0a032 (gold, caution)
--accent = --ibkr #3b86dd (primary buttons, active states, focus, links)
font 'Plus Jakarta Sans' + font-variant-numeric: tabular-nums (global; figures are NOT a mono)
radius 14–16 cards · panels padding ~22–24 · stat sub-cards padding ~14/16
```

## Backend / data notes for the implementer
- **FIRE settings source of truth** should become the Retirement tab (point 5) — verify `fire.py` / `montecarlo.py` read from there, and that the recent investment-buy fix didn't skew the savings-rate / `monthly_expenses` inputs.
- **Income streams** — model each as guaranteed recurring income during the withdrawal phase (offsets spend from its start age, raising "money lasts %"). Inputs: amount, start age, inflation flag. No country-specific formula in core.
- **Forecast accuracy** — keep cash-flow MAPE and net-worth drift as *separate* reads; never show a single blended "accuracy" that the NW market-noise can blow up.

## Out of scope
Budget sub-tab (separate handoff), the Plan tab shell, and all server logic beyond the notes above. This covers the Projections + Retirement view layer only.
