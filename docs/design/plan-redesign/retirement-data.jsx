// retirement-data.jsx — goal-specific FIRE model for the Retirement tab.
// Deterministic FIRE trajectory + stochastic readiness (Monte Carlo w/ withdrawal phase).

// Illustrative seed values for the design mockup — not the owner's real figures.
const START_AGE = 34, START_NW = 210000, FIRE_NUMBER = 1800000, TARGET_AGE = 50;

// FIRE trajectory (deterministic, ~16.5%/yr blended growth+saving) — crosses target ~39.5.
const tAges = [], tNW = [];
for (let a = START_AGE; a <= 44; a++) {
  tAges.push(a);
  tNW.push(Math.round(START_NW * Math.pow(1.165, a - START_AGE)));
}
const crossAge = 39.5;

const FIRE = {
  number: FIRE_NUMBER, progress: 15.8, yearsToFI: 11,
  projectedAge: crossAge, targetAge: TARGET_AGE, monthlyGap: 10355, onTrack: false,
  ages: tAges, nw: tNW, target: FIRE_NUMBER,
};

// Readiness Monte Carlo (40y, 2000 paths, withdrawal phase auto-on from target age).
const ages = [], p10 = [], p50 = [], p90 = [];
for (let a = START_AGE; a <= START_AGE + 40; a++) {
  const t = a - START_AGE;
  ages.push(a);
  p90.push(Math.round(START_NW * Math.pow(1.0835, t)));
  let m;
  if (t <= 18) m = START_NW * Math.pow(1.066, t);
  else m = Math.max(0, START_NW * Math.pow(1.066, 18) * (1 - (t - 18) / 16));
  p50.push(Math.round(m));
  let l;
  if (t <= 9) l = START_NW * Math.pow(1.02, t);
  else l = Math.max(0, START_NW * Math.pow(1.02, 9) * (1 - (t - 9) / 12));
  p10.push(Math.round(l));
}

const READINESS = {
  verdict: 'At risk',                 // On track | Borderline | At risk
  moneyLasts: 41.35,
  pathsRunOut: 58.65,
  nPaths: 2000, horizon: 40,
  ages, p10, p50, p90,
  medianEnding: p50[p50.length - 1],
  p10Ending: p10[p10.length - 1],
  p90Ending: p90[p90.length - 1],
  incomeStreams: [
    { name: 'State pension', from: 'age 65', monthly: 1450 },
    { name: 'Rental income', from: 'now', monthly: 2200 },
  ],
  whatIf: { monthlySaving: 3000, expectedReturn: 7, retirementAge: 50, annualSpend: 72000, volatility: 15, withdrawalRate: 4 },
};

// Saved scenarios for A/B compare (the "save customized projections" feature).
const SCENARIOS = [
  { id: 'a', name: 'Current plan', retireAge: 50, moneyLasts: 41, verdict: 'At risk', tone: 'bad' },
  { id: 'b', name: 'Retire @ 60', retireAge: 60, moneyLasts: 78, verdict: 'On track', tone: 'pos' },
];

window.RET = { FIRE, READINESS, SCENARIOS };
