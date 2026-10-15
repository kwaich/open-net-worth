// projections-data.jsx — REAL model behind the Projections tab
// Two panels: (1) Forecast accuracy [deterministic, backward], (2) Monte-Carlo scenarios [stochastic, forward].
// The Monte Carlo is the SAME engine as Retirement — here it's framed as a generic sandbox (no FIRE verdict).

/* ---------- Panel 1: forecast accuracy (reframed from MAPE/bias/drift) ---------- */
const ACCURACY = {
  monthsTracked: 3,
  // Plain reads, split by what's controllable vs market-driven.
  cashflow: { verdict: 'good', errorPct: 18, note: 'Your monthly cash flow lands within roughly a fifth of plan — close enough to trust.' },
  networth: { verdict: 'noisy', note: 'Net worth swings with the markets, so the projected net-worth line is a rough guide, not a promise.' },
  projectedMonthlyNet: 3000,
  // Per-month plan-vs-actual — illustrative shape (large NW misses are market moves).
  months: [
    { m: 'Mar 2026', cfMiss: -820.61, nwMiss: -4200.54 },
    { m: 'Apr 2026', cfMiss: -540.11, nwMiss: -6800.77 },
    { m: 'May 2026', cfMiss: 310.30, nwMiss: -9100.04 },
  ],
  // The raw technical stats — kept but demoted behind a "show the numbers" affordance.
  tech: { cashflowMape: 18.4, avgMonthlyMiss: 550.34, cashflowBias: -350.14, netWorthDrift: -6700.12 },
};

/* ---------- Panel 2: Monte-Carlo scenario sandbox (40y, 2000 paths) ---------- */
// Illustrative seed values for the design mockup — not the owner's real figures.
const N_PATHS = 2000, HORIZON = 40, START_AGE = 34, START_NW = 210000;
const ages = [], p10 = [], p50 = [], p90 = [];
for (let a = START_AGE; a <= START_AGE + HORIZON; a++) {
  const t = a - START_AGE;
  ages.push(a);
  // p90 (lucky): steady compounding to ~S$11M.
  p90.push(Math.round(START_NW * Math.pow(1.0835, t)));
  // p50 (typical): accumulate to a peak ~age 46, then a withdrawal phase draws it to ~0 (money runs out — 41% last).
  let m;
  if (t <= 18) m = START_NW * Math.pow(1.066, t);
  else m = Math.max(0, START_NW * Math.pow(1.066, 18) * (1 - (t - 18) / 16));
  p50.push(Math.round(m));
  // p10 (unlucky): barely grows, depletes early.
  let l;
  if (t <= 9) l = START_NW * Math.pow(1.02, t);
  else l = Math.max(0, START_NW * Math.pow(1.02, 9) * (1 - (t - 9) / 12));
  p10.push(Math.round(l));
}

const SCENARIO = {
  nPaths: N_PATHS, horizon: HORIZON, ages, p10, p50, p90,
  medianEnding: p50[p50.length - 1],   // ~0
  p10Ending: p10[p10.length - 1],      // 0
  p90Ending: p90[p90.length - 1],      // ~11M
  moneyLasts: 41.35,
  pathsRunOut: 58.65,
  presets: ['Base case', 'Stress test', 'Big purchase'],
  inputs: { expectedReturn: 7, volatility: 15, monthlySaving: 5200 },
};

/* ---------- pointer to the goal-specific tab ---------- */
const RETIRE_POINTER = { fireNumber: 1800000, progress: 15.8, yearsToFI: 11 };

window.PROJ = { ACCURACY, SCENARIO, RETIRE_POINTER };
