# BUILD CORRECTIONS — binding overrides to BUILD-SPEC.md

These are the adversarial-review findings. **On any conflict, this file WINS over BUILD-SPEC.md.**
Before using ANY field name or unit, verify it against the real code (the endpoint's Pydantic
response model under `backend/` and the TS type in `frontend/src/api/insights.ts`). Do not trust a
field path from the spec without checking it exists.

## BLOCKERS (must fix)

1. **`fPct` double-percent / wrong scale.** `fPct` (in `frontend/src/utils/format.ts`) ALREADY appends
   `%`. `success_probability` and `depletion_probability` are **0–1 fractions**. So:
   - Render probabilities as `fPct(value, { fraction: true })` — NO trailing `'%'`, and `fraction: true`
     (not `false`).
   - This applies to: Projections P2 success card, P2 legend "% of paths run out", Projections hero
     stat 3, Retirement R0 hero big %, readiness "Money lasts" card, A/B "money lasts".
   - `fire.progress_pct` is a **0–100** number (used as a bar width `${progress_pct}%`). Render it as
     `fPct(progress_pct / 100, { fraction: true })` OR `Math.round(progress_pct) + '%'` — pick one, no
     double %.
   - FIRST read `format.ts` and confirm `fPct`'s signature + the `fraction` semantics. If they differ
     from this note, follow the code and render a single correct `%`.

2. **No `scorecard.*` wrapper.** The forecast-scorecard response has fields at TOP LEVEL. Use
   `data.projected_monthly_net`, `data.periods`, `data.cashflow_accuracy.*`, `data.net_worth_accuracy.*`.
   There is NO `scorecard` object. Confirm exact names against `backend/api/forecast_scorecard.py` +
   `frontend/src/api/insights.ts` `ForecastScorecard` before wiring.

3. **Preserve the tab-panel wrappers in `Planning.tsx`.** The projections/retirement branches are each
   inside `<div role="tabpanel" id=... aria-labelledby=... hidden={plantab !== ...}>`. Locate the blocks
   by content (the spec's line numbers are stale). ONLY swap the inner `{plantab === '…' && ( … )}` body
   for `<ProjectionsBranch …/>` / `<RetirementBranch …/>`. Do NOT drop the `role`/`aria-labelledby`/`hidden`
   wrappers — that is an a11y + behavior regression.

4. **`Cluster align="flex-end"` is invalid.** `ClusterAlign = 'center' | 'start' | 'end' | 'baseline'`.
   Use `align="end"` everywhere the spec says `"flex-end"`.

5. **`Text tone="muted"` is invalid.** `TextTone` has no `muted` (it has `faint | ink | good | bad | warn`).
   Replace every `<Text tone="muted">` from the spec with `tone="faint"`. (`muted` is only a Badge/Stat
   tone.) Verify the exact union in `frontend/src/components/ui/Text.tsx`.

6. **Every static className must be both DEFINED and APPLIED.** In particular the responsive hide
   `proj-month-col--nw` must be set on the NW header cell AND each NW data cell, or it's dead. Drop or
   apply `proj-month-miss`. After build, there must be zero `lint:classes` violations AND no defined-but-
   unused churn.

## MAJORS (must fix)

7. **`Tag` is NOT clickable** (no `onClick`/`aria-pressed`; only `onRemove`).
   - Quick what-if chips must be real `<button>`s. Foundation adds `.retire-whatif-chip` +
     `.retire-whatif-chip--active` to `index.css` (Step 1 owns CSS). Chips render as
     `<button className={"retire-whatif-chip" + (active ? " retire-whatif-chip--active" : "")} aria-pressed={active}>`.
   - Income-stream chips MAY use `<Tag onRemove={…}>` (onRemove is valid) — that's fine.

8. **Re-home shared formatters BEFORE deleting their files.** Foundation (Step 1) creates
   `frontend/src/utils/retirement.ts` exporting `verdict`, `successLabel`, `successWord`,
   `buildIncomeStreams` — extracted VERBATIM from the current `RetirementReadiness.tsx` and
   `ScenarioPanel.tsx`. `RetirementBranch` imports them from there. Step 4 updates
   `frontend/src/components/__tests__/retirementReadiness.test.ts` to import these from
   `utils/retirement.ts` (drop the test's inlined copies) so the test covers the live code, then deletes
   the old components.

9. **`fire_volatility_pct` validator.** Confirm `backend/models/profile.py` already has the field on
   `Profile` + `ProfileUpdate` (+ blank-coerce). In `backend/api/profile.py`: add
   `fire_volatility_pct: Optional[float] = None` to `ProfileBody`, add `"fire_volatility_pct": None` to
   `DEFAULT_PROFILE`, AND add `"fire_volatility_pct"` to the `_coerce_blank` `@field_validator` field list
   (else a blank form value 422s).

10. **Remove the stale "FIRE inputs … live in Profile ✦" footer.** It must not appear in the new
    `RetirementBranch` (it's contradicted by the editable FIRE-settings block).

11. **`index.css` is single-owner (Step 1).** Steps 2/3 must NOT edit `index.css`. They use only
    Step-1-defined classes; any one-off styling uses an inline `style={{…}}` (lint-safe — the linter only
    scans `className` tokens). Step 4 must NOT edit `index.css` either — fix lint:classes failures by
    correcting the className usage, not by adding rules. If a genuinely-missing class is discovered,
    report it; do not race on the file.

## MINORS (apply)

12. Projections hero stat 3 label: interpolate `data.horizon_years` (e.g. ``Markets · `${data.horizon_years}`-yr
    Monte Carlo``) — do not hardcode "40-yr".
13. `MissBadge`: DROP the `currency` prop (dead — `fBase` already uses the global base currency). Signature
    is `{ n: number }`. Tone modifier from `Math.sign(n)`.
14. Income-stream empty/disabled note: gate on `current_age <= 0`, not `=== undefined` (`/api/fire`
    always returns a numeric `current_age`).
15. 2008-crash what-if chip: pick ONE mechanism (a negative-return year OR a one-off haircut, not both —
    they compound into an unrealistic catastrophe). Label the chip's effect as a rough proxy (mirror the
    old `RetirementReadiness` `presetLabel` approach).
16. `InfoPopover` a11y: use EITHER the fixed scrim OR a document listener for outside-close, not both
    (prefer the scrim). Button gets `aria-expanded` + `aria-controls`; bubble gets `role="tooltip"` and an
    `id`.
17. Complete the import lists — the branch files need (as used): `EmptyState`, `Modal`, `Loading`,
    `ErrorState`, `Grid`, `Tag`, `Panel`, `Badge`, `Button`, `Cluster`, `Stack`, `Text`, `SegmentedControl`,
    `SummaryBar`, `FormField`, `NumberField`, `TextField` — import only what each actually uses.
18. Monthly-saving write-back reads the freshest `monthly_expenses` from the profile fetch at save time,
    not a stale local render.
19. `ForecastScorecard` type is ALREADY exported from `insights.ts` — just import it. Do not add an export
    or claim it as work.
20. Do NOT modify `check-css-classes.mjs` for `num` — `.num` is already covered (ALLOWLIST + matched by
    `td.num`/`th.num` selectors). Only touch the ALLOWLIST if `npm run lint:classes` actually reports a
    real violation; verify first.

## Verification each agent runs (lightweight; never the full `make check` except Wire/Verify)

- Foundation + Screens agents: `cd frontend && npx tsc --noEmit` (only care about errors in files you own
  + foundation files; a sibling branch file being written concurrently may show transient errors — ignore
  those, never edit a file you don't own) and `npm run lint:classes`. Foundation-B also runs a backend
  import smoke + the profile test.
- Wire + Verify: full gate (`make check` equivalent — tsc + vite build + vitest + ruff/import-linter +
  backend pytest).
- Never run `git add/commit` (the orchestrator handles git). Never run `npm run build` concurrently
  (writes `dist/`); use `tsc --noEmit`.
