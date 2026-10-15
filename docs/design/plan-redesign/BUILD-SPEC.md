# Plan Tab — Projections & Retirement Redesign: Build-Ready UI Spec

---

## 1. SCOPE & PLACEMENT

These designs **replace** the existing panel bodies inside the two existing branches of `Planning.tsx`. No new top-level tabs. No new sub-tabs. The Plan tab's sub-tab system (`plantab` in `localStorage`) remains: `budget | projections | retirement`. The existing branch gates stay:

```
{plantab === 'projections' && <ProjectionsBranch total={total} onAskLucius={onAskLucius} />}
{plantab === 'retirement'  && <RetirementBranch  total={total} onAskLucius={onAskLucius} />}
```

`ProjectionsBranch` and `RetirementBranch` move from inline JSX in `Planning.tsx` to named components in their own files. The old `ForecastScorecardPanel` and `ScenarioPanel` shared components are replaced; `RetirementReadiness` and `FireSection` are replaced. `RetirementFanChart` is refactored to be reused on both tabs.

### Files created (net-new)

| Path | Purpose |
|---|---|
| `frontend/src/components/ui/InfoPopover.tsx` | Reusable click-to-open info popover (§2) |
| `frontend/src/components/shared/FanChart.tsx` | Unified SVG fan chart replacing both Chart.js instances |
| `frontend/src/components/shared/TrajectoryChart.tsx` | SVG deterministic trajectory chart (NW line + FIRE target) |
| `frontend/src/components/shared/MissBadge.tsx` | Signed-value tone badge for plan-vs-actual table |
| `frontend/src/components/tabs/planning/ProjectionsBranch.tsx` | Full Projections sub-tab body |
| `frontend/src/components/tabs/planning/RetirementBranch.tsx` | Full Retirement sub-tab body |
| `frontend/src/hooks/useForecastScorecard.ts` | Extracted hook (was useEffect in ForecastScorecardPanel) |
| `frontend/src/hooks/useMonteCarlo.ts` | Extracted hook (was inline fetch in ScenarioPanel / RetirementReadiness) |
| `frontend/src/hooks/useFire.ts` | Extracted hook (was inline fetch in FireSection / RetirementReadiness) |

### Files modified

| Path | Change |
|---|---|
| `frontend/src/components/tabs/Planning.tsx` | Replace inline branch bodies with `<ProjectionsBranch>` / `<RetirementBranch>`. Add `onAskLucius` plumbing to Projections branch. |
| `frontend/src/components/shared/RetirementFanChart.tsx` | Retire — replaced by `FanChart.tsx`. Remove file. |
| `frontend/src/components/shared/FireSection.tsx` | Retire — replaced. Remove file. |
| `frontend/src/components/shared/ForecastScorecardPanel.tsx` | Retire — replaced. Remove file. |
| `frontend/src/components/shared/ScenarioPanel.tsx` | Retire — replaced. Remove file. |
| `frontend/src/components/RetirementReadiness.tsx` | Retire — replaced. Remove file. |
| `frontend/src/index.css` | Add all new `proj-*` and `retire-*` class blocks (see §4) |
| `frontend/scripts/check-css-classes.mjs` | Add any new ALLOWLIST entries with category comments |
| `frontend/src/api/insights.ts` | Export `ForecastScorecard` type for the new hook |

### Backend file modified (minimal, targeted)

| Path | Change |
|---|---|
| `backend/api/profile.py` | Add `fire_volatility_pct: Optional[float]` to `ProfileBody` and `DEFAULT_PROFILE` |

---

## 2. SHARED PRIMITIVES — REUSE vs CREATE

### 2a. Pill / verdict badge → REUSE `Badge`

The prototype's `Pill` component is functionally identical to the existing `Badge` primitive: tone-coded dot + label, `pos|neg|neutral|warn` tones. **Use `<Badge>` from `ui/Badge.tsx` everywhere.** The prototype's `bad` tone maps to `neg`; `info` maps to `neutral`. No new component.

```
prototype: <Pill tone="bad">At risk</Pill>
actual:    <Badge tone="neg">At risk</Badge>
```

### 2b. Info popover → CREATE `frontend/src/components/ui/InfoPopover.tsx`

No `ui/` primitive exists for click-to-open popovers. The existing `.infotip-*` CSS classes are CSS-only hover tooltips (not click-controlled, not accessible as interactive controls). This surface requires click-to-open, outside-click-close, and `aria-expanded`. Create a new kit primitive.

```typescript
interface InfoPopoverProps {
  text: string;
  width?: number; // default 250
}
```

Uses CSS classes: `info-pop-wrap`, `info-pop-btn`, `info-pop-btn--open`, `info-pop-scrim`, `info-pop-bubble`. Defined in `index.css` (see §4). Uses `useRef` + `useState(open)` + document click listener for outside-close.

Barrel export: add to `frontend/src/components/ui/index.ts`.

### 2c. WindowChips → REUSE `SegmentedControl`

The prototype's `WindowChips` is a styled segmented control with options `1M|3M|6M|1Y`. The existing `SegmentedControl` from `ui/SegmentedControl.tsx` already handles this (it was used for window selection in the old `ForecastScorecardPanel`). **Reuse directly.** No new component.

```tsx
<SegmentedControl
  options={[{value:'1M',label:'1M'},{value:'3M',label:'3M'},{value:'6M',label:'6M'},{value:'1Y',label:'1Y'}]}
  value={window}
  onChange={setWindow}
  ariaLabel="Accuracy window"
/>
```

### 2d. MissBadge → CREATE `frontend/src/components/shared/MissBadge.tsx`

A signed-number tone pill specific to the plan-vs-actual table. Not `Badge` (which is not a number formatter) and not `SummaryBar` (which is a stat card). Create a small focused component.

```typescript
interface MissBadgeProps {
  n: number; // signed delta value
  currency: string; // base currency symbol for display
}
```

Renders: `fBase(Math.abs(n))` with sign prefix using `fPct`-style logic. Uses CSS classes `miss-badge`, `miss-badge--pos`, `miss-badge--neg`. Defined in `index.css` (§4).

### 2e. TechToggle → REUSE `CollapsibleSection`

The prototype's "Show the technical numbers" toggle is a disclosure control. The existing `CollapsibleSection` from `ui/CollapsibleSection.tsx` covers this — it wraps a Panel body with an open/close chevron. Use `CollapsibleSection` with `defaultOpen={false}` and title `"Technical accuracy numbers"`. No new component.

### 2f. Scenario preset bar (ScenarioBar) → BUILD ON `SegmentedControl` + `Button`

The scenario preset selector (Base case / Stress test / Big purchase) is the existing three-item `SegmentedControl` pattern already in `ScenarioPanel`. For the "save current" affordance, add a `Button variant="ghost" size="sm"` with `iconLeft="+"` label `"Save scenario"` next to it in a `Cluster`. Saved scenarios appear in the `SegmentedControl` options dynamically. The saved-scenario state is local in-memory only (deferred to §8). No new component; compose from existing primitives.

### 2g. FanChart → REPLACE both chart instances with `frontend/src/components/shared/FanChart.tsx`

The existing `RetirementFanChart` uses Chart.js. The prototype specifies a plain SVG implementation (exact math in Map 5 §11). This avoids Chart.js registration boilerplate, makes the chart portable, and eliminates hardcoded hex colors.

**Reuse `RetirementFanChart`?** No — it carries Chart.js dependency, baked-in color literals, and an incorrectly-ignored `baseCurrency` prop. Replace it with a new SVG-based component.

```typescript
interface FanChartProps {
  bands: Array<{ year_index: number; age?: number; p10: number; p50: number; p90: number }>;
  secondBands?: Array<{ year_index: number; age?: number; p10: number; p50: number; p90: number }>;
  retirementAge?: number | null;
  baseCurrency: string;
  loading?: boolean;
  height?: number; // default 220; Retirement panel uses 200
  showDryMarker?: boolean; // Projections: true; Retirement: determined by has_withdrawal_phase
}
```

SVG math: exactly as in Map 5 §11. Colors via CSS custom properties, not hex literals (`stroke: var(--good)`, `fill: var(--good)`, `stroke: var(--warn)`, `stroke: var(--bad)`). The retirement-age vertical line is an SVG `<line>` element (not an absolute-positioned div). Classes: `fan-chart-wrap`, `fan-chart-loading`.

### 2h. TrajectoryChart → CREATE `frontend/src/components/shared/TrajectoryChart.tsx`

The deterministic FIRE trajectory (NW line + FIRE target dashed line + crossing dot) is a separate chart shape from the fan chart. FireSection's existing Chart.js instance is replaced with a plain SVG component.

```typescript
interface TrajectoryChartProps {
  trajectory: Array<{ year: number; age: number; nw: number; target: number }>;
  fireNumber: number;
  loading?: boolean;
  height?: number; // default 196
}
```

SVG math: exactly as in Map 5 §16. Colors: NW line/area `var(--good)`, target line `var(--warn)`, crossing dot `var(--warn)`. No Chart.js dependency. Classes: `traj-chart-wrap`.

### 2i. IncomeStreams → NEW local component within `RetirementBranch.tsx`

The income-stream chip list + "Add income" affordance is specific to the Retirement Monte Carlo panel. It is NOT a shared component (no other tab uses it). Implement as a sub-component `IncomeStreamBar` inside `RetirementBranch.tsx`, not extracted to `shared/`. Uses `Cluster`, `Tag` (for chips, `tone="muted"`), and `Button variant="ghost" size="sm"` for "+ Add income". No new file.

### 2j. QuickWhatIfs → LOCAL component in `RetirementBranch.tsx`

Four preset chips (Retire 5 yrs earlier / Bad sequence / Save +$1k/mo / 2008-style crash). Implemented as `QuickWhatIfBar` sub-component inside `RetirementBranch.tsx`. Chips use `Tag tone="accent"` for active state and `Tag tone="muted"` for inactive. No new file; no new primitive.

### 2k. SavedCompare → LOCAL component in `RetirementBranch.tsx`

The A/B compare section (`SavedCompareSection`) lives in `RetirementBranch.tsx`. Uses `Grid` (min=300), `Panel`, `Badge`, `Button`. Local in-memory state for `scenarioB`. No new file.

### 2l. ProgressBar → REUSE existing `.fire-progress-bar` / `.fire-progress-wrap`

The existing `fire-progress-bar` and `fire-progress-wrap` CSS classes in `index.css` already define a thin progress track. Reuse these directly via a single `<span>` render pattern. No new component; use the existing classes.

### 2m. AccuracyRead sub-card → LOCAL component in `ProjectionsBranch.tsx`

The two-up left-accent sub-cards (Cash flow reliable / Net worth noisy) are specific to the forecast accuracy panel. Implement as a local `AccuracyRead` sub-component inside `ProjectionsBranch.tsx`. Uses CSS classes `acc-read`, `acc-read--pos`, `acc-read--neg`, `acc-read--warn` (§4). No new file.

### 2n. HeroStat → LOCAL component in each branch

The `HeroStat` (label + large value + sub line) appears in both tabs but with different data shapes. Implement as a local `HeroStat` sub-component inside each branch file — they are close but not identical enough to justify a shared primitive (Projections version has no `.num` on value; Retirement version has `.num`).

### 2o. FireSettings → LOCAL component in `RetirementBranch.tsx`

The editable FIRE settings block (Annual spend / SWR / Expected return / Volatility / Target age / Monthly saving + Save/Reset) lives inside `RetirementBranch.tsx` as `FireSettingsBlock`. Uses `FormField`, `NumberField`, `Button`, `Grid`. Writes via `PUT /api/profile` (scoped fields). No new file.

---

## 3. SCREEN SPECS

### 3a. Projections Sub-Tab

**File:** `frontend/src/components/tabs/planning/ProjectionsBranch.tsx`

**Mount in Planning.tsx:**
```tsx
{plantab === 'projections' && (
  <ProjectionsBranch total={total} onAskLucius={onAskLucius} />
)}
```

**Props:**
```typescript
interface ProjectionsBranchProps {
  total: number;
  onAskLucius?: (prompt: string) => void;
}
```

**Top-level layout:** `<Stack gap="--space-6">` containing:
- Region P0: Hero strip
- Region P1: Panel — Forecast accuracy
- Region P2: Panel — Monte Carlo sandbox
- Region P3: Lucius hint

---

**Region P0 — Hero strip**

Wrapper: `<div className="proj-hero">` (see §4 for class body).

Contents (left to right in a `<Cluster gap="--space-8" align="flex-end" wrap={true}>`):

- `<Badge tone="pos">Forecasts on point</Badge>` (or `tone="warn"/"neg"` based on `cf.mean_abs_pct_error` threshold: `< 20` → pos, `< 40` → warn, else neg — derived client-side).
- Hero question: `<Text size="sm" tone="muted">"How reliable are our forecasts — and what could markets do to your money?"</Text>`
- Three `HeroStat` values (right-aligned, `align="flex-end"`):
  1. Label `"Cash-flow forecast"` / Value = qualitative string (`"Reliable"` if MAPE < 20, `"Noisy"` if 20–40, `"Unreliable"` if > 40) / Sub = `"within ~{round(mape)}% of plan"` / Tone = `--good` / `--warn` / `--bad`.
  2. Label `"Net-worth forecast"` / Value = `"Market-driven"` / Sub = `"swings with markets"` / Tone `--warn`. (Static label — always the same copy because NW is always market-driven; the `nw.bias` drives the sub copy.)
  3. Label `"Markets · 40-yr Monte Carlo"` / Value = `fPct(success_probability, {fraction: true}) + " lasts"` / Sub = `"money survival"` / Tone based on probability: ≥ 0.80 → `var(--good)`, ≥ 0.50 → `var(--warn)`, else `var(--bad)`. / `<InfoPopover text={INFO_MONTE_CARLO_PROJECTIONS}>`.

Data sources: `useForecastScorecard(window)` (cf.mean_abs_pct_error → hero 1), static for hero 2, `useMonteCarlo(params)` (success_probability → hero 3).

Loading state: hero strip renders skeleton values (`—`) while both hooks are loading. No full-page loading state — individual panels below show their own `<Loading>` states.

**640px reflow:** Cluster wraps; hero stats stack vertically below the badge/question.

---

**Region P1 — Panel: Forecast accuracy**

```tsx
<Panel
  title="How close have our forecasts been?"
  actions={
    <SegmentedControl
      options={WINDOW_OPTS}
      value={window}
      onChange={setWindow}
      ariaLabel="Accuracy window"
    />
  }
>
```

**P1 Body states:**
- Loading: `<Loading label="Scoring forecasts…" />`
- Error: `<ErrorState message="Couldn't load the forecast scorecard." onRetry={retry} />`
- Not forecastable (`forecastable === false`, `has_baseline === false`): `<Text size="sm" tone="muted">Not enough transaction history to score forecasts yet.</Text>`
- Not forecastable (`forecastable === false`, `has_baseline === true`): `<Text size="sm" tone="muted">Forecasts exist but no elapsed months to score yet.</Text>`
- Scored: `<Stack gap="--space-5">` containing:

**P1 Body (scored) — top to bottom:**

1. **AccuracyReads (two-up grid):**
   `<div className="proj-accuracy-reads">` with two `AccuracyRead` sub-cards:
   - Left: `tone="pos"` (if MAPE < 20) or `tone="warn"`, `head="Cash flow — reliable"`, `body="Your monthly cash flow lands within roughly a fifth of plan — close enough to trust."`, `foot="Typically within ~{round(mape)}% of plan"`. Data: `cf.mean_abs_pct_error` (MAPE).
   - Right: `tone="warn"`, `head="Net worth — market-driven"`, `body="Net worth swings with the markets, so the projected net-worth line is a rough guide, not a promise."`, `foot="Treat the projected line loosely"`.

2. **MonthTable:**
   `<div className="proj-month-table">`:
   - Header row `<div className="proj-month-row proj-month-row--head">`: `Month` / `Cash-flow miss` / `Net-worth miss`.
   - Data rows (`.map` on `scorecard.periods`): `<div className="proj-month-row">` → `<Text size="sm" tone="muted">{p.label}</Text>` / `<MissBadge n={p.cashflow_delta} currency={baseCurrency} />` / (p.net_worth_delta !== null ? `<MissBadge n={p.net_worth_delta} currency={baseCurrency} />` : `<Text size="sm" tone="faint">—</Text>`).
   - Empty periods (`periods.length === 0`): `<Text size="sm" tone="faint">No periods scored yet.</Text>`

3. **TechToggle (CollapsibleSection):**
   ```tsx
   <CollapsibleSection title="Technical accuracy numbers" defaultOpen={false}>
   ```
   Inside: `<Grid min={180} gap="--space-3">` with four `Tech` sub-cards (rendered as `Panel`-less boxes using `className="proj-tech-card"`):
   - `"Cash-flow error (MAPE)"` / `fPct(cf.mean_abs_pct_error)` / no tone.
   - `"Avg monthly miss"` / `fBase(cf.mean_abs_error)` / no tone.
   - `"Cash-flow bias"` / `fSignedBase(cf.bias)` / tone: `cf.bias < 0 ? 'neg' : 'pos'`.
   - `"Net-worth drift"` / `fSignedBase(nw.bias)` / tone: `nw.bias < 0 ? 'neg' : 'pos'` (only rendered if `nw.periods_scored > 0`).
   - `fSignedBase(n)`: local helper — `(n >= 0 ? '+' : '') + fBase(n)`.

4. **Footnote:** `<Text size="2xs" tone="faint">We projected ~{fBase(scorecard.projected_monthly_net)}/mo net. Actuals from closed months only.</Text>`

**640px reflow:** AccuracyReads grid collapses to 1 column; MonthTable columns collapse (hide NW miss column on < 640px via `proj-month-row--head` / `proj-month-col--nw` responsive classes).

---

**Region P2 — Panel: Monte Carlo sandbox**

```tsx
<Panel
  title={
    <Cluster gap="--space-2" align="center">
      <span>Monte Carlo — what could markets do to your money?</span>
      <InfoPopover text={INFO_MONTE_CARLO_PROJECTIONS} />
    </Cluster>
  }
  subtitle={
    data
      ? `${data.n_paths.toLocaleString()} paths · ${data.horizon_years} yrs`
      : undefined
  }
>
```

**P2 Body — top to bottom:**

1. **Preset + save bar:**
   `<Cluster gap="--space-2" align="center" wrap={true}>`:
   - `<SegmentedControl options={presetOptions} value={preset} onChange={handlePresetChange} ariaLabel="Scenario preset" />` where `presetOptions` = built-in presets + any saved scenarios (STUB: saved array always empty in this release — see §8).
   - `<Button variant="ghost" size="sm" onClick={handleSaveCurrent}>+ Save scenario</Button>` (STUB: logs to console in this release).

2. **Input fields:** `<Grid min={160} gap="--space-3">` with three (or four) `<FormField>` + `<NumberField>` pairs:
   - `"Expected return %"` / id `mc-return` / `value={params.annual_return_pct ?? ''}` / `placeholder="from profile"` / min 0 max 30 step 0.5.
   - `"Market swings (volatility) %"` / id `mc-vol` / `value={params.annual_volatility_pct ?? ''}` / `placeholder="15"` / min 0 max 60 step 1.
   - `"Monthly saving"` / id `mc-contrib` / `value={params.monthly_contribution ?? ''}` / `placeholder="from profile"` / step 50.
   - `"One-off spend"` / id `mc-spend` / only rendered when `preset === 'purchase'` / `placeholder="0"` / min 0 step 1000.

3. **Loading (first load):** `<Loading label="Running simulation…" />` — only when `loading && !data`.

4. **Error (first load):** `<ErrorState message="Couldn't run the simulation." onRetry={retry} />` — only when `error && !data`.

5. **Empty (no total):** `<EmptyState title="Add accounts or holdings" body="Add net worth data to project your money forward." />` — when `!total`.

6. **Results block** (rendered when `total > 0 && data`; wrapped in `<div className="proj-results" style={{opacity: loading ? 0.55 : 1, transition: 'opacity 0.15s ease'}}`):

   a. **Stat cards:** `<SummaryBar cards={statCards} />` where `statCards` (4):
      - `{label: 'Typical outcome', value: fBaseCompact(data.median_ending), tone: 'pos'}` + `mention` carrying Info `p50` text (STUB: `SummaryBar` does not support inline InfoPopover — render as a second row or skip for now; see §8).
      - `{label: 'If unlucky', value: fBaseCompact(data.p10_ending), tone: 'neg'}`.
      - `{label: 'If lucky', value: fBaseCompact(data.p90_ending)}` (no tone).
      - `successCard` — label and tone derived from `successLabel(data)` (same logic as current ScenarioPanel): `success_basis === 'not_depleted'` → `{label:'Money lasts', value: fPct(data.success_probability,{fraction:false}), tone: data.success_probability>=0.8?'pos':data.success_probability>=0.5?'warn':'neg'}`; `meets_target` → `{label:'Hits target', value: fPct(data.success_probability,{fraction:false}), tone: data.success_probability>=0.5?'pos':'warn'}`; else `{label:'Median ending', value: fBaseCompact(data.median_ending), tone:'pos'}`.

   b. **FanChart:** `<FanChart bands={data.bands} baseCurrency={data.base_currency} height={220} showDryMarker={true} loading={loading} />`

   c. **Legend:** `<Cluster gap="--space-4" wrap={true}>` with `<Text size="xs" tone="faint">…</Text>` spans:
      - `"—— typical (p50)"` (styled with `color: var(--good)`).
      - `"▩ p10–p90 range"` (styled `color: var(--good); opacity: 0.4`).
      - If `data.has_withdrawal_phase && data.depletion_probability > 0`: `fPct(data.depletion_probability, {fraction: false}) + "% of paths run out"` (styled `color: var(--bad)`).

   d. **RetirementPointer:** `<div className="proj-retirement-ptr">` (§4):
      - `<Text size="sm">Free-form sandbox — for your actual FIRE plan & verdict</Text>`
      - `<button className="proj-ptr-link" onClick={() => /* set plantab to 'retirement' */}>Open Retirement →</button>` — NOTE: this requires `Planning.tsx` to pass a `onSwitchTab` callback or expose the `setPlantab` setter. The build agent must thread this down.

7. **Lucius hint (Region P3):**
   ```tsx
   <Text size="xs" tone="faint" as="p" className="proj-ai-hint">
     {onAskLucius
       ? <button className="fire-ai-hint-btn" onClick={() => onAskLucius(LUCIUS_HINT_PROJECTIONS)}>
           Ask Lucius: "stress-test a 2008-style crash", "can I afford an S$80k car?", "what if I save S$1k more a month?"
         </button>
       : <>Ask Lucius: "stress-test a 2008-style crash", "can I afford an S$80k car?", "what if I save S$1k more a month?"</>
     }
   </Text>
   ```
   `LUCIUS_HINT_PROJECTIONS = 'stress-test a 2008-style crash'` (first prompt; user can edit in chat).

**640px reflow:** Stat cards wrap to 2×2. Inputs wrap to 1 col. Preset bar wraps.
**1024px+:** Full 4-up stat cards. Input grid 3 columns.

---

### 3b. Retirement Sub-Tab

**File:** `frontend/src/components/tabs/planning/RetirementBranch.tsx`

**Mount in Planning.tsx:**
```tsx
{plantab === 'retirement' && (
  <RetirementBranch total={total} onAskLucius={onAskLucius} />
)}
```

**Props:**
```typescript
interface RetirementBranchProps {
  total: number;
  onAskLucius?: (prompt: string) => void;
}
```

**Top-level layout:** `<Stack gap="--space-6">` containing:
- Region R0: Verdict hero
- Region R1: Panel — Your path to FIRE (trajectory + FIRE settings)
- Region R2: Panel — Monte Carlo readiness
- Region R3: Lucius hint

---

**Region R0 — Verdict hero**

Data: `useFire(total)` → `fire` object; `useMonteCarlo(retirementParams)` → `mc` object. Both must resolve before the hero renders meaningfully. Show `<Loading label="Computing retirement outlook…" />` while either is loading.

Wrapper: `<div className="retire-hero">`.

**Left block: big number**
`<div className="retire-hero-big">`:
- `<span className="retire-hero-pct num">{fPct(mc.success_probability, {fraction: false})}%</span>` — color via `retire-hero-pct--pos/--warn/--neg` modifier (≥ 80 pos, ≥ 50 warn, < 50 neg).
- `<span className="retire-hero-label">chance your money lasts</span>`
- `<InfoPopover text={INFO_MONEY_LASTS_RETIREMENT} />`

**Middle: verdict badge + question**
- `<Badge tone={verdictTone}>{verdictLabel}</Badge>` where `verdictLabel/tone` follows existing `verdict()` logic: ≥ 80% → `"On track"` / `pos`; ≥ 50% → `"Borderline"` / `warn`; else `"At risk"` / `neg`.
- `<Text size="sm" tone="muted">Can you retire at {fire.retirement_target_age}, and will the money last?</Text>`
- If `mc.has_withdrawal_phase && mc.depletion_probability > 0`: `<Text size="xs" tone="bad">Flat return says you hit FIRE at {fire.projected_fire_age ?? '—'} — but Monte Carlo shows the 4% rule may not survive a long retirement. Consider a lower withdrawal rate.</Text>` (the "FIRE reached ≠ safe" bridge line).

**Right block: two HeroStats**
`<Cluster gap="--space-8" align="flex-end">`:
1. `HeroStat` label `"Projected retirement"` / value `"age {fire.projected_fire_age?.toFixed(1) ?? '—'}"` / sub `"target {fire.retirement_target_age}"` / tone: on-track → `var(--good)`, else `var(--warn)`.
2. `HeroStat` label `"To hit target age"` / value `fire.monthly_savings_needed !== null ? "+" + fBase(fire.monthly_savings_needed) : "On track"` / sub `"more / month"` / tone: on-track → `var(--good)`, else `var(--warn)`.

**640px reflow:** Big number and hero stats stack vertically. Badge/question between them.

---

**Region R1 — Panel: Your path to FIRE**

```tsx
<Panel
  title={
    <Cluster gap="--space-2" align="center">
      <span>Your path to FIRE</span>
      <InfoPopover text={INFO_FIRE_NUMBER} />
    </Cluster>
  }
  actions={<Badge tone={onTrack ? 'pos' : 'neg'}>{onTrack ? 'On track' : 'Off track'}</Badge>}
>
```

**R1 Body — top to bottom:**

1. **FIRE summary cards:** `<SummaryBar cards={fireCards} />` where `fireCards`:
   - `{label: 'FIRE number', value: fBaseCompact(fire.fire_number)}` + `mention` for Info (see STUB note in §5).
   - `{label: 'Progress', value: fPct(fire.progress_pct / 100, {fraction: false}) + '%'}` — rendered alongside a `<span className="fire-progress-wrap"><span className="fire-progress-bar" style={{width: `${Math.min(100, fire.progress_pct)}%`, background: 'var(--warn)'}} /></span>`.
   - `{label: 'Years to FI', value: fire.years_to_fire?.toFixed(1) ?? '—'}` + Info `yearsToFI`.
   - `{label: 'Median outcome (p50)', value: fBaseCompact(mc.median_ending)}`.

2. **TrajectoryChart:** `<TrajectoryChart trajectory={fire.trajectory} fireNumber={fire.fire_number} loading={fireLoading} height={196} />`

3. **FIRE settings block** (divider, then editable):
   `<div className="retire-settings-block">`:

   **Header row** `<Cluster justify="between" wrap={true}>`:
   - `<Cluster gap="--space-2" align="baseline">` → `<Text size="xs" tone="faint" weight={600} className="retire-settings-eyebrow">FIRE SETTINGS</Text>` + `<Text size="xs" tone="faint">now editable here</Text>`
   - `<Cluster gap="--space-2">` → `<Button variant="ghost" size="sm" onClick={resetSettings}>Reset</Button>` + `<Button variant="primary" size="sm" onClick={saveSettings} loading={saving}>Save settings</Button>`

   **Derived FIRE number line:**
   `<Text size="xs" tone="muted" as="p" className="retire-fire-derived">`
   `FIRE number = annual spend ÷ withdrawal rate = `
   `<span className="num" style={{color:'var(--ink)'}}>` `{fBase(localSettings.annual_spend)} ÷ {localSettings.withdrawal_rate}%`
   `</span>` ` = `
   `<span className="num" style={{color:'var(--ink)', fontWeight:600}}>` `{fBaseCompact(derivedFireNumber)}`
   `</span>`
   where `derivedFireNumber = localSettings.annual_spend / (localSettings.withdrawal_rate / 100)`.

   **Fields:** `<Grid min={160} gap="--space-3">` with six `<FormField>` + `<NumberField>` pairs:
   - `"Annual spend in retirement"` / id `fire-spend` / step 1000 / `placeholder="from profile"`.
   - `"Safe withdrawal rate %"` + `<InfoPopover text={INFO_WITHDRAWAL_RATE}>` in label / id `fire-swr` / min 1 max 10 step 0.1.
   - `"Expected return %"` / id `fire-return` / min 0 max 20 step 0.5.
   - `"Return volatility %"` / id `fire-vol` / min 0 max 60 step 1.
   - `"Target retirement age"` / id `fire-age` / min 18 max 100 step 1.
   - `"Monthly saving"` / id `fire-saving` / step 50.

   These bind to local `settingsState`, and `Save settings` calls `PUT /api/profile` with the fire fields. (See §5 for exact fields + NEEDS-BACKEND note.)

**R1 States:**
- `fireLoading && !fire`: `<Loading label="Computing FIRE trajectory…" />`
- `fireError && !fire`: `<ErrorState message="Couldn't compute FIRE trajectory." onRetry={retryFire} />`

---

**Region R2 — Panel: Monte Carlo readiness**

```tsx
<Panel
  title={
    <Cluster gap="--space-2" align="center">
      <span>Monte Carlo — will it survive a real market?</span>
      <InfoPopover text={INFO_MONTE_CARLO_RETIREMENT} />
    </Cluster>
  }
  subtitle={
    mc
      ? `${mc.n_paths.toLocaleString()} paths · withdrawal phase ${mc.has_withdrawal_phase ? 'on' : 'off'}`
      : undefined
  }
>
```

**R2 Body — top to bottom:**

1. **Income streams bar:**
   `<Cluster gap="--space-2" wrap={true} className="retire-income-bar">`:
   - For each stream in `incomeStreams`: `<Tag tone="muted" onRemove={() => removeStream(i)}>` showing `{stream.label || 'Income'} +{fBase(stream.monthly_amount)}/mo from age {stream.start_age}`.
   - `<button className="retire-add-income" onClick={openAddIncomeModal}>+ Add income</button>`
   - Help text `<Text size="xs" tone="faint">Pension, CPF, Social Security, rental — offsets withdrawals</Text>`

   Add/edit income stream opens a `<Modal size="sm" title="Add income stream">` with `FormField`+`TextField` (label), `NumberField` (monthly amount), `NumberField` (start age). On confirm: appends to `incomeStreams` local state → triggers MC re-run.

   NOTE: `stream.start_age` is converted to `start_year = start_age - fire.current_age` before sending to `POST /api/montecarlo` (see §5).

2. **Readiness stat cards:** `<SummaryBar cards={readinessCards} />` where `readinessCards` (3 cards matching prototype):
   - `{label: 'Typical', value: fBaseCompact(mc.median_ending), tone: 'pos'}`.
   - `{label: 'If lucky', value: fBaseCompact(mc.p90_ending)}`.
   - `{label: 'Money lasts', value: fPct(mc.success_probability, {fraction: false}) + '%', tone: mc.success_probability >= 0.8 ? 'pos' : mc.success_probability >= 0.5 ? 'warn' : 'neg'}`.

3. **FanChart with retirement age line:**
   `<FanChart bands={mc.bands} secondBands={scenarioB?.bands} retirementAge={retirementAgeForLine} baseCurrency={baseCurrency} height={200} showDryMarker={mc.has_withdrawal_phase} loading={mcLoading} />`
   where `retirementAgeForLine = fire?.retirement_target_age ?? (fire?.current_age && whatIf.retire_after_years ? fire.current_age + whatIf.retire_after_years : null)`.

4. **Quick what-ifs section:**
   `<div className="retire-whatif-section">`:
   - `<Text size="xs" tone="faint" weight={600} className="retire-whatif-eyebrow">QUICK WHAT-IFS</Text>`
   - `<Cluster gap="--space-2" wrap={true}>` with four `QuickWhatIf` chips (toggle active/inactive, single-select with re-click to deselect):
     - `"Retire 5 yrs earlier"` → `retire_after_years -= 5` (disabled if would result in ≤ 5 years).
     - `"Bad sequence of returns"` → `annual_return_pct: 2, annual_volatility_pct: 25`.
     - `"Save +{fBase(1000)}/mo"` → `monthly_contribution += 1000`.
     - `"2008-style crash"` → `annual_return_pct: -20, annual_volatility_pct: 35` (single-year effect approximated as `one_off_spend: total * 0.5`).
   - `<Text size="2xs" tone="faint">preview only — doesn't change your saved plan</Text>`
   - `<Button variant="ghost" size="sm" onClick={resetWhatIf}>Reset to saved plan</Button>`

5. **A/B compare section:**
   `<div className="retire-compare-section">`:
   - Header `<Cluster justify="between" align="center">` → `<Text size="sm" weight={600}>Compare saved plans</Text>` + `<Button variant="ghost" size="sm" onClick={saveAsB}>+ Save current as plan B</Button>`.
   - If `scenarioB` is set: `<Grid min={300} gap="--space-3">` with two scenario cards (`.retire-compare-card`) for Scenario A (current/live) and Scenario B (saved snapshot). Each card: `Badge` verdict + `<span className="retire-compare-pct num">{moneyLasts}%</span>` + `"money lasts"` label + `"Retire at age {retireAge}"` footer. Clear button `<Button variant="ghost" size="sm" onClick={clearB}>Clear comparison</Button>`.
   - If no `scenarioB`: hint `<Text size="xs" tone="faint">Save a scenario above to compare it with your current plan.</Text>`

6. **Loading states:**
   - MC first-load: `<Loading label="Running retirement simulation…" />` when `mcLoading && !mc`.
   - MC re-run (stale-while-revalidate): results block dims to `opacity: 0.55` while `mcLoading && mc`.
   - MC error: `<ErrorState message="Couldn't run the retirement simulation." onRetry={retryMc} />` when `mcError && !mc`.

7. **Empty (no total):** `<EmptyState title="Add net worth data" body="Add accounts or holdings to run a retirement simulation." />` when `!total`.

**R3 — Lucius hint:**
```tsx
<Text size="xs" tone="faint" as="p">
  {onAskLucius
    ? <button className="fire-ai-hint-btn" onClick={() => onAskLucius(LUCIUS_HINT_RETIREMENT)}>
        Ask Lucius: "what if I retire at 45?", "how much more must I save to hit 80% confidence?"
      </button>
    : <>Ask Lucius: "what if I retire at 45?", "how much more must I save to hit 80% confidence?"</>
  }
</Text>
```
`LUCIUS_HINT_RETIREMENT = 'what if I retire at 45?'`

**640px reflow:** Stat cards 2-up. Income chips wrap. What-if chips wrap. Compare grid stacks to 1 column.
**1024px+:** Full 3-up stat cards. What-if chips inline.

---

## 4. CSS PLAN

All new classes live in `frontend/src/index.css` in a clearly labeled block:

```css
/* ================================================================
   Plan tab — Projections sub-tab  (proj-*)
   ================================================================ */
```

and:

```css
/* ================================================================
   Plan tab — Retirement sub-tab  (retire-*)
   ================================================================ */
```

and:

```css
/* ================================================================
   Shared ui/InfoPopover  (info-pop-*)
   ================================================================ */
```

and:

```css
/* ================================================================
   Shared MissBadge  (miss-badge*)
   ================================================================ */
```

These blocks go AFTER the existing `/* fire-* */` block and BEFORE any future additions. Each block is self-contained.

### InfoPopover classes

```css
.info-pop-wrap {
  position: relative;
  display: inline-flex;
  vertical-align: middle;
}

.info-pop-btn {
  width: 16px;
  height: 16px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--line);
  background: transparent;
  color: var(--faint);
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  cursor: pointer;
  font: inherit;
  flex-shrink: 0;
}

.info-pop-btn--open {
  background: color-mix(in srgb, var(--accent) 15%, transparent);
  color: var(--accent);
  border-color: var(--accent);
}

.info-pop-scrim {
  position: fixed;
  inset: 0;
  z-index: 50;
}

.info-pop-bubble {
  position: absolute;
  top: calc(100% + var(--space-1));
  left: 0;
  z-index: 51;
  background: var(--surface2);
  border: 1px solid var(--modal-border);
  border-radius: var(--radius-lg);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  line-height: 1.5;
  color: var(--muted);
  font-weight: 400;
  box-shadow: var(--shadow-modal);
  white-space: normal;
  text-wrap: pretty;
  min-width: 220px;
}
```

### MissBadge classes

```css
.miss-badge {
  display: inline-block;
  font-size: var(--text-xs);
  padding: 2px var(--space-2);
  border-radius: var(--radius-pill);
  font-variant-numeric: tabular-nums;
}

.miss-badge--pos {
  color: var(--good);
  background: color-mix(in srgb, var(--good) 15%, transparent);
}

.miss-badge--neg {
  color: var(--bad);
  background: color-mix(in srgb, var(--bad) 15%, transparent);
}
```

### Projections classes

```css
.proj-hero {
  padding: var(--space-4) var(--space-5);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-xl);
}

.proj-accuracy-reads {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}

@media (max-width: 640px) {
  .proj-accuracy-reads {
    grid-template-columns: 1fr;
  }
}

.acc-read {
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-lg);
  background: var(--bg);
  border: 1px solid var(--line);
  border-left-width: 2px;
}

.acc-read--pos { border-left-color: var(--good); }
.acc-read--warn { border-left-color: var(--warn); }
.acc-read--neg { border-left-color: var(--bad); }

.acc-read-head {
  font-size: var(--text-sm);
  font-weight: 600;
}
.acc-read--pos .acc-read-head { color: var(--good); }
.acc-read--warn .acc-read-head { color: var(--warn); }
.acc-read--neg .acc-read-head { color: var(--bad); }

.acc-read-body {
  margin: var(--space-1) 0 var(--space-2);
  font-size: var(--text-xs);
  color: var(--muted);
  line-height: 1.5;
}

.acc-read-foot {
  font-size: 11px;
  color: var(--faint);
}

.proj-month-table {
  display: grid;
  grid-template-columns: 1fr 150px 150px;
  gap: 0;
}

@media (max-width: 640px) {
  .proj-month-table {
    grid-template-columns: 1fr 120px;
  }
  .proj-month-col--nw {
    display: none;
  }
}

.proj-month-row {
  display: contents;
}

.proj-month-row > * {
  padding: var(--space-2) 2px;
  border-top: 1px solid var(--line);
  font-size: var(--text-sm);
  display: flex;
  align-items: center;
}

.proj-month-row--head > * {
  border-top: none;
  padding-bottom: var(--space-2);
  font-size: 10.5px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--faint);
  font-weight: 600;
}

.proj-month-miss {
  justify-content: flex-end;
}

.proj-tech-card {
  padding: var(--space-2) var(--space-3);
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.proj-tech-label {
  font-size: 10.5px;
  color: var(--faint);
}

.proj-tech-val {
  font-size: var(--text-base);
  font-weight: 600;
  margin-top: var(--space-1);
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

.proj-tech-val--neg { color: var(--bad); }

.proj-results {
  transition: opacity 0.15s ease;
}

.proj-retirement-ptr {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-lg);
  background: var(--bg);
  border: 1px solid var(--line);
}

.proj-ptr-link {
  background: none;
  border: none;
  color: var(--accent);
  font-size: var(--text-sm);
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
  padding: 0;
  font: inherit;
  flex-shrink: 0;
}

.proj-ptr-link:hover { text-decoration: underline; }

.proj-ai-hint {
  font-style: italic;
}
```

### Retirement classes

```css
.retire-hero {
  padding: var(--space-5) var(--space-6);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-xl);
  display: flex;
  align-items: flex-end;
  gap: var(--space-8);
  flex-wrap: wrap;
}

@media (max-width: 640px) {
  .retire-hero {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-4);
  }
}

.retire-hero-big {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.retire-hero-pct {
  font-size: 44px;
  font-weight: 700;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

.retire-hero-pct--pos { color: var(--good); }
.retire-hero-pct--warn { color: var(--warn); }
.retire-hero-pct--neg { color: var(--bad); }

.retire-hero-label {
  font-size: var(--text-sm);
  color: var(--muted);
}

.retire-settings-block {
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid var(--line);
}

.retire-settings-eyebrow {
  font-size: 10.5px;
  letter-spacing: 0.13em;
  text-transform: uppercase;
}

.retire-fire-derived {
  margin: 0 0 var(--space-3);
  font-size: var(--text-xs);
  color: var(--faint);
}

.retire-income-bar {
  margin-bottom: var(--space-4);
}

.retire-add-income {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-md);
  border: 1px dashed var(--modal-border);
  background: transparent;
  color: var(--faint);
  font-size: var(--text-xs);
  cursor: pointer;
  font: inherit;
}

.retire-add-income:hover { color: var(--muted); border-color: var(--line); }

.retire-whatif-section {
  margin-top: var(--space-5);
  padding-top: var(--space-5);
  border-top: 1px solid var(--line);
}

.retire-whatif-eyebrow {
  font-size: 10.5px;
  letter-spacing: 0.13em;
  text-transform: uppercase;
  margin-bottom: var(--space-3);
}

.retire-compare-section {
  margin-top: var(--space-5);
  padding-top: var(--space-5);
  border-top: 1px solid var(--line);
}

.retire-compare-card {
  padding: var(--space-4);
  border-radius: var(--radius-xl);
  background: var(--bg);
  border: 1px solid var(--line);
}

.retire-compare-card--winner {
  border-color: var(--good);
}

.retire-compare-pct {
  font-size: 26px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.retire-compare-pct--pos { color: var(--good); }
.retire-compare-pct--neg { color: var(--bad); }

.fan-chart-wrap {
  display: block;
  width: 100%;
}

.fan-chart-loading {
  opacity: 0.55;
}

.traj-chart-wrap {
  display: block;
  width: 100%;
}
```

### ALLOWLIST additions to `check-css-classes.mjs`

Add the following (with category comments) to the ALLOWLIST:

- `"num"` — `[pre-existing]` tabular-numeric utility, used in prototype patterns; rendered dynamically in some spots but also as static className in chart labels. Add as `[global]` since it is a single utility class used across tabs.

The `color-mix()` usage in CSS is not a className token — the linter only checks className strings, not CSS values — so no ALLOWLIST entry is needed for that.

---

## 5. DATA MAPPING TABLE

| UI element | API field | Endpoint | Status |
|---|---|---|---|
| CF MAPE headline | `cashflow_accuracy.mean_abs_pct_error` | `GET /api/forecast-scorecard?window=X` | READY |
| CF avg monthly miss | `cashflow_accuracy.mean_abs_error` | same | READY |
| CF bias | `cashflow_accuracy.bias` | same | READY |
| NW drift bias | `net_worth_accuracy.bias` (gated on `net_worth_accuracy.periods_scored > 0`) | same | READY |
| Per-month cashflow delta | `periods[].cashflow_delta` | same | READY |
| Per-month NW delta | `periods[].net_worth_delta` | same | READY |
| Projected monthly net (footnote) | `scorecard.projected_monthly_net` | same | READY |
| MC median ending | `median_ending` | `GET /api/montecarlo` | READY |
| MC p10/p90 ending | `p10_ending` / `p90_ending` | same | READY |
| MC money-lasts % | `success_probability` (when `success_basis === 'not_depleted'`) | same | READY |
| MC path count | `n_paths` | same | READY |
| MC horizon years | `horizon_years` | same | READY |
| MC fan bands | `bands[].{year_index, age, p10, p50, p90}` | same | READY |
| MC depletion % | `depletion_probability` | same | READY |
| MC has withdrawal phase | `has_withdrawal_phase` | same | READY |
| FIRE number | `fire_number` | `GET /api/fire?current_nw=X` | READY |
| FIRE progress % | `progress_pct` | same | READY |
| Years to FI | `years_to_fire` | same | READY |
| Projected fire age | `projected_fire_age` | same | READY |
| Target retirement age | `retirement_target_age` | same | READY |
| Monthly gap | `monthly_savings_needed` | same | READY |
| On track | `on_track` | same | READY |
| Trajectory data | `trajectory[].{year, age, nw, target}` | same | READY |
| Current age (for income stream start_year conversion) | `current_age` | `GET /api/fire` | READY |
| Income streams → MC | `income_streams[].{start_year, monthly_amount}` | `POST /api/montecarlo` (body) | READY (POST only) |
| Income stream start_age → start_year | `start_year = start_age - fire.current_age` | client-side conversion | READY |
| Income stream inflation flag | per-stream `inflation: boolean` | not supported | NEEDS-BACKEND (add field to `validate_income_streams` in `services/montecarlo.py` + indexing in `_build_income_vector`; low risk, scope < 30 lines) |
| FIRE settings — monthly retirement spending | `fire_monthly_retirement_spending` | `PUT /api/profile` | READY |
| FIRE settings — SWR | `fire_withdrawal_rate_pct` | `PUT /api/profile` | READY |
| FIRE settings — expected return | `fire_expected_return_pct` | `PUT /api/profile` | READY |
| FIRE settings — volatility | `fire_volatility_pct` | `PUT /api/profile` | NEEDS-BACKEND: add `fire_volatility_pct: Optional[float] = None` to `ProfileBody` and `DEFAULT_PROFILE` in `backend/api/profile.py` |
| FIRE settings — target age | `retirement_target_age` | `PUT /api/profile` | READY |
| FIRE settings — monthly saving | `monthly_net_income` - `monthly_expenses` (write both) | `PUT /api/profile` | READY (but UI must communicate that saving = income - expenses; display as monthly saving, persist as monthly_net_income by adding saving to expenses) |
| MC "Save current" scenario | persisted scenario object | no endpoint | STUB (local in-memory only; `savedScenarios` state in component; logs to console on save) |
| Scenario A/B compare | client-side `scenarioB` snapshot | no endpoint | STUB (in-memory `scenarioB` state matching current RetirementReadiness pattern) |
| "Retire @ 45" saved preset chip | named scenario with params | no endpoint | STUB (hardcoded built-in presets only; saved ✦ presets deferred) |
| Projections "Open Retirement →" | switch to retirement sub-tab | `setPlantab('retirement')` via callback | READY (needs `onSwitchTab` prop threaded from Planning.tsx) |
| MC one_off_events (multi-year) | `one_off_events[].{year, amount}` | `POST /api/montecarlo` | READY (POST only) — not exposed in UI in this release |
| SummaryBar inline InfoPopover | StatCard InfoPopover slot | `SummaryBar` component | STUB — `SummaryBar` does not support an `info` slot on `StatCard`; defer inline Info icons on summary cards; use caption field for abbreviated note |

**Monthly saving write path clarification:** The profile stores `monthly_net_income` and `monthly_expenses` separately. The UI exposes a single "Monthly saving" field. On save: `monthly_net_income = monthly_expenses + entered_saving`. This avoids the implementation needing to know the split. The existing `GET /api/fire` already reads both and computes `monthly_savings = monthly_net_income - monthly_expenses`.

---

## 6. EMPTY / LOADING / ERROR STATES

### `useForecastScorecard` hook

| State | Trigger | Component |
|---|---|---|
| Loading | `loading === true` | `<Loading label="Scoring forecasts…" />` inside Panel body (title + SegmentedControl remain visible) |
| Error | `error && !data` | `<ErrorState message="Couldn't load the forecast scorecard." onRetry={retry} />` |
| Not forecastable (no baseline) | `data.forecastable === false && !data.data_quality.has_baseline` | `<Text size="sm" tone="muted">Not enough transaction history to score forecasts yet.</Text>` |
| Not forecastable (no elapsed months) | `data.forecastable === false && data.data_quality.has_baseline` | `<Text size="sm" tone="muted">Forecasts exist but no elapsed months to compare yet.</Text>` |
| Empty periods | `data.forecastable && data.periods.length === 0` | `<Text size="sm" tone="faint">No periods scored in this window.</Text>` in the month table slot |
| Stale revalidation | new window selected, prior data present | AccuracyReads + table remain visible at `opacity: 0.55`; Loading replaces the table section |

### `useMonteCarlo` hook (Projections)

| State | Trigger | Component |
|---|---|---|
| No net worth | `!total` | `<EmptyState title="Add accounts or holdings" body="Add net worth data to project your money forward." />` |
| First load | `mcLoading && !mcData` | `<Loading label="Running simulation…" />` |
| Error (no prior data) | `mcError && !mcData` | `<ErrorState message="Couldn't run the simulation." onRetry={retry} />` |
| Stale revalidation | params changed, prior data present | Results block dims to `opacity: 0.55` |

### `useFire` hook (Retirement)

| State | Trigger | Component |
|---|---|---|
| First load | `fireLoading && !fireData` | `<Loading label="Computing FIRE trajectory…" />` replaces Panel body |
| Error | `fireError && !fireData` | `<ErrorState message="Couldn't compute FIRE trajectory." onRetry={retryFire} />` |
| No DOB set | `fireData.current_age === undefined` | Income streams silently excluded from MC call (existing behavior); no error shown but InfoPopover on income streams section says "Set your date of birth in Profile to enable income streams" (static note in the help text) |

### `useMonteCarlo` hook (Retirement)

| State | Trigger | Component |
|---|---|---|
| No net worth | `!total` | `<EmptyState title="Add net worth data" body="Add accounts or holdings to run a retirement simulation." />` |
| First load | `mcLoading && !mcData` | `<Loading label="Running retirement simulation…" />` |
| Error (no prior data) | `mcError && !mcData` | `<ErrorState message="Couldn't run the retirement simulation." onRetry={retryMc} />` |
| Stale revalidation | income streams or what-ifs changed, prior data present | Fan chart and stat cards dim to `opacity: 0.55`; small `<Text size="2xs" tone="faint">Re-running…</Text>` caption below stat cards |

### Verdict hero (Retirement R0)

| State | Trigger | Component |
|---|---|---|
| Either loading | `fireLoading \|\| (mcLoading && !mcData)` | `<Loading label="Computing retirement outlook…" />` replaces entire hero |
| Fire error | `fireError && !fireData` | Hero big number shows `—`; verdict badge shows `<Badge tone="neutral">—</Badge>` |

---

## 7. CONFLICT-FREE BUILD ORDER

All steps run on the single working tree. Steps are sequential — each step owns specific files and does not touch files owned by another step. The uncommitted WIP in `Planning.tsx`, `planning/*.tsx`, `useBudget.ts`, `index.css`, and `format.ts` (visible in the git status) must be preserved; all edits to those files are additive (new blocks, new branches) or surgical.

---

### Step 1 — Foundation: shared primitives + CSS (owns: ui/InfoPopover.tsx, shared/FanChart.tsx, shared/TrajectoryChart.tsx, shared/MissBadge.tsx, index.css new blocks, ui/index.ts, check-css-classes.mjs)

1a. Add all new CSS class blocks to `frontend/src/index.css` in this exact order, each under its labeled comment block, after the existing `/* fire-* */` block:
- `/* info-pop-* */` block
- `/* miss-badge* */` block
- `/* proj-* */` block
- `/* retire-* */` block
- `/* fan-chart* traj-chart* */` block

1b. Create `frontend/src/components/ui/InfoPopover.tsx` per §2b spec. Export from `ui/index.ts` barrel.

1c. Create `frontend/src/components/shared/FanChart.tsx` per §2g spec (SVG, no Chart.js). Props interface as specified. Colors via `var(--good)`, `var(--warn)`, `var(--bad)`.

1d. Create `frontend/src/components/shared/TrajectoryChart.tsx` per §2h spec (SVG, no Chart.js).

1e. Create `frontend/src/components/shared/MissBadge.tsx` per §2d spec. Uses `fBase` from `utils/format.ts`.

1f. Create the three hooks:
- `frontend/src/hooks/useForecastScorecard.ts` — wraps the `fetchForecastScorecard(window)` call (from `api/insights.ts`); returns `{ data, loading, error, retry }`. Signature: `useForecastScorecard(window: ScorecardWindow)`.
- `frontend/src/hooks/useMonteCarlo.ts` — wraps `POST /api/montecarlo` (uses `authFetch`); returns `{ data, loading, error, retry }`. Signature: `useMonteCarlo(params: ScenarioParams | null)` — when `params` is null (no total), skips fetch. Types for `ScenarioParams` and `ScenarioResult` move here from inline-in-ScenarioPanel.
- `frontend/src/hooks/useFire.ts` — wraps `GET /api/fire?current_nw=X` (uses `authFetch`); returns `{ data, loading, error, retry }`. Signature: `useFire(currentNw: number)` — when `currentNw <= 0`, skips fetch.

1g. Add `"num"` to ALLOWLIST in `frontend/scripts/check-css-classes.mjs` with comment `// [global] tabular-numeric utility class`. Check if already present before adding.

1h. Apply the backend fix: add `fire_volatility_pct: Optional[float] = None` to `ProfileBody` in `backend/api/profile.py`, and add `"fire_volatility_pct": None` to `DEFAULT_PROFILE`. Run `make lint` on backend to confirm no import-linter violations.

**Verify step 1:** `npm run lint:classes` must pass with zero violations. All new files must pass `tsc --noEmit`.

---

### Step 2 — Projections screen (owns: planning/ProjectionsBranch.tsx only)

2a. Create `frontend/src/components/tabs/planning/ProjectionsBranch.tsx`.

Contains: local sub-components `HeroStat`, `AccuracyRead`, `Tech` (all unexported). Imports: `InfoPopover`, `FanChart`, `MissBadge` from Step 1; `useForecastScorecard`, `useMonteCarlo` from Step 1; all ui/ primitives; `fBase`, `fBaseCompact`, `fPct` from `utils/format`.

Logic wired in this file:
- `window` state (default `'3M'`) → `useForecastScorecard(window)`.
- `preset` state (default `'base'`) + `overrides` state → derived `params` → `useMonteCarlo(total > 0 ? params : null)`.
- `selectPreset(p)` clears overrides; `setOv(k, v)` sets one override.
- `handleSaveCurrent()` → STUB: `console.log('Save scenario', params)`.
- `onSwitchTab` prop (type `() => void`) wired to `proj-ptr-link` click.

Preset constants (exact):
```typescript
const PRESETS = [
  { value: 'base', label: 'Base case' },
  { value: 'stress', label: 'Stress test' },
  { value: 'purchase', label: 'Big purchase' },
];
const PRESET_DEFAULTS: Record<string, Partial<ScenarioParams>> = {
  base: {},
  stress: { annual_return_pct: 3, annual_volatility_pct: 22 },
  purchase: { one_off_spend: 50000 },
};
```

Info tooltip constants (exact copy from §2 catalog):
```typescript
const INFO_MONTE_CARLO_PROJECTIONS = "A Monte Carlo simulation runs thousands of random market histories instead of assuming one fixed return — so you see a realistic range of outcomes, not a single optimistic line.";
```

**Verify step 2:** `tsc --noEmit` passes. `npm run lint:classes` passes.

---

### Step 3 — Retirement screen (owns: planning/RetirementBranch.tsx only)

3a. Create `frontend/src/components/tabs/planning/RetirementBranch.tsx`.

Contains: local sub-components `HeroStat`, `FireSettingsBlock`, `IncomeStreamBar`, `QuickWhatIfBar`, `SavedCompareSection` (all unexported). Imports: `InfoPopover`, `FanChart`, `TrajectoryChart` from Step 1; `useFire`, `useMonteCarlo` from Step 1; all ui/ primitives; `fBase`, `fBaseCompact`, `fPct`.

Local state in `RetirementBranch`:
- `incomeStreams: IncomeStream[]` (default `[]`).
- `whatIf: WhatIfState` (default all undefined).
- `activeChip: string | null` (quick what-if, default null).
- `scenarioB: { params: ScenarioParams; result: ScenarioResult } | null` (default null).
- `localSettings: FireSettings` (initialized from profile on load, local edit buffer).
- `saving: boolean` (profile save in flight).
- `addingIncome: boolean` (modal open).

`PUT /api/profile` call in `saveSettings()` — fields: `fire_monthly_retirement_spending`, `fire_withdrawal_rate_pct`, `fire_expected_return_pct`, `fire_volatility_pct`, `retirement_target_age`. After save, invalidate `useFire` and `useMonteCarlo` by bumping a `refreshKey` counter passed as a param.

Income stream `start_year` conversion: `start_year = Math.max(0, Math.round(stream.start_age - (fireData?.current_age ?? 65)))`.

MC params for retirement: always `POST /api/montecarlo` with `retire_after_years` set from `localSettings.retirement_target_age - fireData.current_age`, `monthly_withdrawal = localSettings.monthly_retirement_spending`, `income_streams` when non-empty.

Info tooltip constants (exact):
```typescript
const INFO_MONTE_CARLO_RETIREMENT = "A Monte Carlo simulation runs thousands of random market histories — including the retirement withdrawal phase — so you see how often your money survives, not one optimistic line.";
const INFO_FIRE_NUMBER = "Your FIRE number = the savings that can fund your lifestyle indefinitely (annual spending ÷ a safe withdrawal rate, here 4%).";
const INFO_YEARS_TO_FI = "Years until your projected net worth reaches your FIRE number, at your assumed return and saving rate.";
const INFO_WITHDRAWAL_RATE = "The share of your savings you withdraw each year in retirement. The classic \"4% rule\" is calibrated for ~30 years — retiring early stretches it over far longer, so a lower rate is safer.";
const INFO_MONEY_LASTS_RETIREMENT = "Share of simulations where your money lasted through retirement without hitting zero. Above ~80% is comfortable; this is well below.";
```

**Verify step 3:** `tsc --noEmit` passes. `npm run lint:classes` passes.

---

### Step 4 — Wire into Planning.tsx + retire old files (owns: Planning.tsx, deleted component files)

4a. In `frontend/src/components/tabs/Planning.tsx`:

Replace the `{plantab === 'projections' && (...)}` inline block (current lines 116–121) with:
```tsx
{plantab === 'projections' && (
  <ProjectionsBranch
    total={total}
    onAskLucius={onAskLucius}
    onSwitchTab={() => setPlantab('retirement')}
  />
)}
```

Replace the `{plantab === 'retirement' && <RetirementBranch ...>}` block (current lines 124–134) with:
```tsx
{plantab === 'retirement' && (
  <RetirementBranch
    total={total}
    onAskLucius={onAskLucius}
  />
)}
```

Remove the old inline `RetirementBranch` function (lines 497–551) and the old `ProjectionsBranch` inline JSX. Remove imports for `FireSection`, `RetirementReadiness`, `ForecastScorecardPanel`, `ScenarioPanel`. Add imports for `ProjectionsBranch`, `RetirementBranch`.

Thread `setPlantab` to `ProjectionsBranch` via `onSwitchTab` prop. The `setPlantab` setter already exists in Planning's local state — confirm its scope and pass it. Add `onSwitchTab?: () => void` to `ProjectionsBranchProps`.

4b. Delete retired files (the build agent removes these and updates any barrel imports):
- `frontend/src/components/shared/ForecastScorecardPanel.tsx`
- `frontend/src/components/shared/ScenarioPanel.tsx`
- `frontend/src/components/shared/FireSection.tsx`
- `frontend/src/components/RetirementReadiness.tsx`
- `frontend/src/components/shared/RetirementFanChart.tsx`

If any of these are referenced in test files (e.g. `retirementReadiness.test.ts`), the test file must be updated or deleted. The existing test `frontend/src/__tests__/retirementReadiness.test.ts` (if it imports `RetirementReadiness`) should be replaced with a smoke test for `RetirementBranch` that mocks both `useFire` and `useMonteCarlo`.

4c. Verify full build: `make check` (lint + type + test + build). Fix any remaining `lint:classes` violations by adding missing class definitions to `index.css` (Step 1 classes should cover all new static classNames; confirm no new ones were introduced in Steps 2–3).

---

## 8. OUT OF SCOPE / DEFERRED

**Saved scenario persistence.** The "Save current" button on Projections and "Save current as plan B" on Retirement are STUB in this release — local in-memory state only, lost on navigation. Implementing scenario persistence requires a full vertical slice (new `db/scenario_store.py`, `services/scenarios.py`, `api/scenarios.py` router per Map 3 §5c). Ship the button and UI shell; log to console; tag with a `// TODO(scenarios): wire to POST /api/scenarios` comment.

**Income stream inflation indexing.** The `inflation: boolean` per-stream field is not supported by `POST /api/montecarlo`. The Add Income modal in this release omits the inflation toggle. Tag with `// TODO(inflation): add indexed field when backend supports it`.

**SummaryBar inline InfoPopover.** The `StatCard` type does not have an `info` slot. Info popovers on FIRE summary cards (FIRE number, Years to FI) are deferred; use `caption` for abbreviated explanations. A future design-system extension can add an `info?: string` field to `StatCard`.

**Country-specific income estimators.** The prototype's "Pension, CPF, Social Security…" subtitle is the extent of it. No CPF-specific defaults, calculators, or data lookups are in scope. The income stream label is free text.

**One-off events (multi-year).** `POST /api/montecarlo` supports `one_off_events[].{year, amount}` but the UI in this release does not expose them. Deferred — out of scope for this redesign.

**Per-country withdrawal-rate guidance.** The `INFO_WITHDRAWAL_RATE` copy references "4% rule calibrated for ~30 years" as static text. No locale-specific guidance is generated dynamically.

**`FIRE settings source-of-truth` full migration.** The spec uses `PUT /api/profile` directly (the existing route, minimal change). The "fuller" migration to a dedicated `/api/retirement-settings` router (Map 3 §5a) is deferred. The `fire_volatility_pct` backend fix in Step 1h is the only backend change required.

**`num` className as a design-system utility.** The `.num` class is used inline as a `className` prop on `<span>` elements for tabular-numeric figures throughout. It is not defined in `index.css` today — it is in the ALLOWLIST. Rather than add a rule body for a single property, this release keeps it in the ALLOWLIST as `[global]`. A future CSS cleanup can promote it to a defined utility.
