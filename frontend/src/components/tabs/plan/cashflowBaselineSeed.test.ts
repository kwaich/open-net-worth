// Regression: the Cash Flow tab's what-if must be OPT-IN. An empty income/expense box means
// "use the server's baseline"; only a value the user actually typed is an override.
//
// The bug this pins: What-If Active could light up even though neither box had anything
// typed into it, and tapping Reset All did not visibly change anything.
//
// `buildUrl` sends `income_override` whenever the box is non-empty. The tab used to AUTO-SEED
// that box with the server's baseline on first load and persist it to localStorage, so:
//   * every request carried an override → server replied `simulation_active: true` →
//     "WHAT-IF ACTIVE" lit permanently, for a user who had set nothing;
//   * the persisted number PINNED the projection, overriding the live baseline forever — and
//     after the stream model (.claude/rfcs/2026-07-27-cashflow-stream-model.md) redefined
//     what that baseline means, it pinned a figure the engine no longer even computes;
//   * "Reset all" wrote the baseline number back INTO the box, so it visibly did nothing.
//
// These read the source rather than rendering: the defect is *which value is written*, and a
// rendered assertion would pass against either as soon as a fixture made them coincide.
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const hook = readFileSync(new URL('./useCashflowData.ts', import.meta.url), 'utf8')
const tab = readFileSync(new URL('./cashflow.tsx', import.meta.url), 'utf8')

describe('Cash Flow what-if is opt-in, never auto-seeded', () => {
  it('never auto-fills the income/expense boxes on load', () => {
    expect(hook).not.toMatch(/if \(!incomeVal\) setIncomeVal\(/)
    expect(hook).not.toMatch(/if \(!expenseVal\) setExpenseVal\(/)
  })

  it('still seeds the safety floor, which is profile-backed and not an override', () => {
    // 343b1ead1 ("Make first-run empty states honest and concise") widened this from a
    // one-time seed (`if (!floorVal) setFloorVal(...)`) to tracking the server's own value on
    // every change — a still-blank box, or one still equal to the LAST server value seeded,
    // keeps following the server's (profile-backed) floor; a value the user actually typed
    // over it is left alone.
    expect(hook).toContain('lastServerFloor')
    const effect = hook.slice(hook.indexOf('const serverFloor ='))
    const body = effect.slice(0, effect.indexOf('}, [serverFloor])'))
    expect(body).toMatch(
      /current === '' \|\| \(previous != null && current === String\(Math\.round\(previous\)\)\)/,
    )
    expect(body).toMatch(/\? String\(Math\.round\(serverFloor\)\)\s*:\s*current/)
  })

  it('Reset all clears the overrides instead of writing a number back', () => {
    const reset = hook.slice(hook.indexOf('function resetSettings'))
    const body = reset.slice(0, reset.indexOf('load('))
    expect(body).toMatch(/const di = ''/)
    expect(body).toMatch(/const de = ''/)
    expect(body).not.toContain('monthly_income_avg')
    expect(body).not.toContain('actual_income_avg')
  })

  it('the Baseline preset applies NO override', () => {
    // Sliced up to the object's closing brace, not the first newline — prettier wraps each
    // preset's fields onto their own line (dc9d7d986), so a single-line slice would only ever
    // see the `key:` field itself.
    const baseline = tab.slice(tab.indexOf("key: 'baseline'"))
    const obj = baseline.slice(0, baseline.indexOf('}'))
    expect(obj).toMatch(/incomeVal: ''/)
    expect(obj).toMatch(/expenseVal: ''/)
  })

  it('a preset only overrides the side it actually changes', () => {
    // "Pay raise"/"High inflation" vary growth only — pinning the level would freeze the
    // scenario against a snapshot of today's numbers.
    for (const key of ['raise', 'inflation']) {
      const rest = tab.slice(tab.indexOf("key: '" + key + "'"))
      const spec = rest.slice(0, rest.indexOf('}'))
      expect(spec, key).toMatch(/incomeVal: ''/)
      expect(spec, key).toMatch(/expenseVal: ''/)
    }
    // "Lean spending" genuinely changes expenses; "Lose income" genuinely zeroes income.
    const lean = tab.slice(tab.indexOf("key: 'lean'"))
    expect(lean.slice(0, lean.indexOf('}'))).toContain('expenseVal: LEAN')
    const lose = tab.slice(tab.indexOf("key: 'lose'"))
    expect(lose.slice(0, lose.indexOf('}'))).toMatch(/incomeVal: '0'/)
  })
})

describe('persisted what-if state is versioned so stale overrides cannot survive', () => {
  it('drops the old income/expense overrides when the stored version is behind', () => {
    expect(hook).toContain('LS_VERSION')
    const loader = hook.slice(hook.indexOf('function loadSaved'))
    const body = loader.slice(0, loader.indexOf('\n}'))
    expect(body).toMatch(/saved\['v'\] !== LS_VERSION/)
    expect(body).toContain("delete saved['incomeVal']")
    expect(body).toContain("delete saved['expenseVal']")
  })

  it('writes the version back so the migration runs exactly once', () => {
    expect(hook).toMatch(/v: LS_VERSION/)
  })

  it('keeps genuine user choices across the upgrade', () => {
    const loader = hook.slice(hook.indexOf('function loadSaved'))
    const body = loader.slice(0, loader.indexOf('\n}'))
    for (const kept of [
      'collapsedYears',
      'filter',
      'includeFixedDeposits',
      'includeInvestments',
      'includeCommitments',
    ]) {
      expect(body, kept).not.toContain("delete saved['" + kept + "']")
    }
  })
})

describe('the baseline shown is the projection own assumption', () => {
  it('quick-scenario anchors read monthly_*_avg, not the retired flat average', () => {
    expect(tab).toMatch(/BASE_IN = Math\.round\(data\.monthly_income_avg\)/)
    expect(tab).toMatch(/BASE_OUT = Math\.round\(data\.monthly_expenses_avg\)/)
  })
})

describe('expanded months explain where the recurring total comes from', () => {
  it('lists recurring streams instead of stripping them from display', () => {
    // They were hidden when the server sent the whole baseline as two opaque blobs. Each
    // detected stream is now its own named event (Salary, Rent, Spotify) — the only thing
    // that makes "+S$26.94K recurring in" checkable rather than asserted.
    expect(hook).not.toMatch(
      /m\.events\.filter\(e => e\.status !== 'recurring'\)\s*;?\s*\n\s*return \{/,
    )
    expect(hook).toContain(
      'const displayEvents = [...scheduled, ...recurringNamed, ...variableBuckets]',
    )
    expect(hook).toMatch(/recurring: e\.status === 'recurring'/)
  })

  it('separates the averaged VARIABLE buckets into their own trailing group', () => {
    // "Irregular income" / "Everyday spending" are 3-month averages over many items, not
    // bills. Left unmarked they trailed off the end of the named-stream list with no divider
    // (the two irregular buckets need a divider so they read as a distinct trailing group).
    // (e) — prettier's arrow-parens default (dc9d7d986, "prettier --write across frontend/src").
    expect(hook).toMatch(
      /variableBuckets = m\.events\.filter\(\(e\) => e\.status === 'recurring' && !!e\.variable\)/,
    )
    expect(hook).toMatch(/variable: !!e\.variable/)
    expect(tab).toContain('Variable • averaged')
    expect(tab).toMatch(/startsVariable = !!prev && !!ev\.variable && !prev\.variable/)
  })

  it('still derives recIn/recOut from the non-recurring events, so the header total is unchanged', () => {
    expect(hook).toMatch(/recIn: Math\.max\(0, m\.total_in - evIn\)/)
    expect(hook).toMatch(/recOut: Math\.max\(0, m\.total_out - evOut\)/)
  })

  it('labels the in-progress month as a partial month, since its recurring figure is prorated', () => {
    // Jul showed "S$3.48K recurring in" against Aug's S$26.94K — correct (4 of 31 days left,
    // the rest already in actuals) but unexplainable without saying so.
    expect(tab).toContain('daysLeftLabel(row.ym)')
    expect(tab).toContain('Rest of this month •')
    // The retired wording claimed the listed items were scheduled; nothing in the list is.
    // NOTE: assertions here avoid backticks on purpose — check-css-classes.mjs pairs template
    // literals with a naive backtick scan across all source, so a lone backtick inside a regex
    // shifts that pairing and silently degrades dead-CSS detection repo-wide.
    expect(tab).not.toContain('Scheduled this month')
    expect(tab).toContain('What happened this month')
    expect(tab).toContain('Expected this month •')
    expect(tab).toContain('left of ${inMonth}')
    // …and only for the month actually in progress, never a future one.
    expect(tab).toMatch(
      /y !== today\.getFullYear\(\) \|\| m !== today\.getMonth\(\) \+ 1\) return ''/,
    )
  })
})
