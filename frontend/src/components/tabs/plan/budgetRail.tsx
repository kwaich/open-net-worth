// plan/budgetRail.tsx — the desktop-only (≥1024px) right-hand rail on the Budget tab: two
// always-visible panels that otherwise only exist as click-to-open UI (the Ready-to-Assign
// breakdown popover, and the Auto-assign dropdown). "[Month]'s Summary" mirrors real YNAB's
// own month-totals card (owner ask, 2026-09-15) — the RTA identity breakdown that used to live
// here moved back to being popover-only (click the RTA figure in the hero), since YNAB keeps
// the two as separate surfaces and showing both here was redundant. The Auto-assign panel is
// still a thin shell over buildAutoAssignItems — zero math re-derived here.
import * as PD from './planData'
import { Panel } from '../../ui'
import { stripYear } from './rtaLines'
import { monthLabel } from '../../../hooks/useBudget'
import { buildAutoAssignItems, type AutoBudget } from './budgetParts'

const { fBase } = PD

// ── Month totals (owner ask, 2026-09-15) — the SAME shape as an individual envelope row
// (Left over + Assigned + Activity = Available), just summed across every envelope, mirroring
// real YNAB's own "[Month]'s Summary" card. Reuses the table's own `totals` — no separate
// computation — so this can never drift from the Total row at the bottom of the table.
// "Left over from last month" isn't a stored figure of its own; it's the one term the other
// three don't carry, so it's derived the same way every individual row's Available already is
// (`available = carriedIn + assigned + activity`), solved for the unknown term.
function MonthTotalsBlock({
  totalAssigned,
  totalActivity,
  totalAvailable,
  totalTargets,
}: {
  totalAssigned: number
  totalActivity: number
  totalAvailable: number
  totalTargets: number
}) {
  const leftOver = Math.round((totalAvailable - totalAssigned - totalActivity) * 100) / 100
  const availNeg = totalAvailable < -0.005,
    availZero = Math.abs(totalAvailable) <= 0.005
  return (
    <>
      <div className="pl-rtapop-row">
        <span className="lbl">Left over from last month</span>
        <span className="val mask">{fBase(leftOver)}</span>
      </div>
      <div className="pl-rtapop-row">
        {/* Not "Assigned in September" — the panel's own title already says whose month
            this is (owner ask, 2026-09-15). Every row here is about that one month; naming
            it again on each line is noise. */}
        <span className="lbl">Assigned</span>
        <span className="val mask">{fBase(totalAssigned)}</span>
      </div>
      <div className="pl-rtapop-row">
        <span className="lbl">Activity</span>
        <span className="val mask">{fBase(totalActivity)}</span>
      </div>
      <div className="pl-rtapop-div" />
      <div
        className={
          'pl-rtapop-total pl-rtapop-total--month' +
          (availZero ? ' zero' : availNeg ? ' neg' : ' pos')
        }
      >
        <span className="lbl">Available</span>
        <span className="val mask">{fBase(totalAvailable)}</span>
      </div>
      {/* Real YNAB's "Cost to Be Me" figure (owner ask, 2026-09-15) — what fully funding every
          target this month COSTS, sitting below the totals it is deliberately NOT part of: the
          four lines above are what happened to the money, this is what the plan asks for. One
          self-describing row rather than YNAB's heading-plus-row, which said the same thing
          twice. Always rendered, $0.00 included — a book with no targets should say so. */}
      <div className="pl-rtapop-div pl-rtapop-div--targets" />
      <div className="pl-rtapop-row pl-rtapop-row--targets">
        <span className="lbl">This month's targets</span>
        <span className="val mask">{fBase(totalTargets)}</span>
      </div>
    </>
  )
}

// ── Panel 1 — "[Month]'s Summary" — the month totals only (owner ask, 2026-09-15: dropped
// the RTA identity breakdown that used to render here too — real YNAB keeps that as its own
// popover, click the RTA figure in the hero, and showing both here was redundant). ──────────
type BudgetSummaryTerms = {
  /** Gates the loading state — same "data not ready" signal RtaHero uses elsewhere. */
  onBudgetCash: number | null
  monthLabel: string
  totalAssigned: number
  totalActivity: number
  totalAvailable: number
  /** What fully funding every target this month COSTS — see buildTargetsTotal in budget.tsx.
   *  Independent of what's assigned so far, so it doesn't move as you fund things. */
  totalTargets: number
}
export function BudgetSummaryPanel(terms: BudgetSummaryTerms) {
  return (
    // Titled the way real YNAB titles its own card — "September's Summary", the month named
    // rather than "this month" (owner ask, 2026-09-15). stripYear keeps it to the bare month,
    // the same convention the rows below and the RTA popover already use.
    <Panel collapsible defaultCollapsed={false} title={`${stripYear(terms.monthLabel)}'s Summary`}>
      {/* Data isn't ready yet — same guard RtaHero uses, so a half-loaded month looks
          loading rather than an empty/broken breakdown. */}
      {terms.onBudgetCash == null ? (
        <div className="pl-budget-rail-loading">Loading…</div>
      ) : (
        <MonthTotalsBlock
          totalAssigned={terms.totalAssigned}
          totalActivity={terms.totalActivity}
          totalAvailable={terms.totalAvailable}
          totalTargets={terms.totalTargets}
        />
      )}
    </Panel>
  )
}

// ── Panel 3 — "Assigned in Future Months" (owner ask, 2026-09-15, matching real YNAB) — one
// row per future month with ANY assignment data, RAW per-month sums (never the RTA identity's
// clamped deduction — this is a read-only ledger of what's already been set aside ahead, not
// a recomputation of what it's costing THIS month). Hidden entirely when there's nothing to
// show, same convention as the Underfunded stat above.
export function BudgetFutureAssignedPanel({
  data,
}: {
  data: Array<{ month: string; amount: number }>
}) {
  if (!data.length) return null
  const total = Math.round(data.reduce((sum, d) => sum + d.amount, 0) * 100) / 100
  return (
    <Panel
      collapsible
      defaultCollapsed={false}
      title="Assigned in Future Months"
      badge={<span className="mask">{fBase(total)}</span>}
    >
      <div className="pl-budget-rail-pills">
        {data.map((d) => (
          <div key={d.month} className="pl-budget-rail-stat">
            <span>{stripYear(monthLabel(d.month))}</span>
            <span className="mask">{fBase(d.amount)}</span>
          </div>
        ))}
      </div>
    </Panel>
  )
}

// ── Panel 2 — "Auto-assign" — the toolbar dropdown's own modes. Card-payment debt is folded
// into the "Underfunded" hint itself (owner ask, 2026-09-15) rather than a separate stat row —
// two "Underfunded"-shaped rows read as a confusing near-duplicate; one row, one number matches
// real YNAB exactly. See AutoBudget.cardUnderfundedAmount / buildAutoAssignItems. ───────────
export function BudgetAutoAssignPanel({
  onPick,
  b,
}: {
  onPick: (label: string) => void
  b: AutoBudget
}) {
  const items = buildAutoAssignItems(b)
  return (
    <Panel collapsible defaultCollapsed={false} title="Auto-Assign">
      {/* Rail-only pill rows (owner feedback #2) — each its own rounded chip, separated by the
          container's gap rather than the dropdown's divider lines, so the `__div` group marker
          (splitting Underfunded / the history modes / the 2 resets) is dropped here; a plain
          gap between .pl-budget-rail-pills children reads as the same grouping, and the
          destructive resets' own `.danger` red text still carries that distinction. */}
      <div className="pl-budget-rail-pills">
        {items.map(([label, hint, danger], i) =>
          label === '__div' ? (
            // A visible gap between groups (Underfunded / the 4 history modes / the 2
            // resets), matching real YNAB's own blank-line spacing — not a divider LINE,
            // which would look wrong against these self-contained pill rows.
            <div key={i} className="pl-budget-rail-group-gap" />
          ) : (
            <button
              key={i}
              className={'pl-menu-item pl-budget-rail-pill' + (danger ? ' danger' : '')}
              onClick={() => onPick(label)}
            >
              <span>{label}</span>
              {hint && <span className="hint">{hint}</span>}
            </button>
          ),
        )}
      </div>
    </Panel>
  )
}
