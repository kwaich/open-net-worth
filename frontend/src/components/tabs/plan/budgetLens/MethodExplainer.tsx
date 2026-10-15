// budgetLens/MethodExplainer.tsx — a method's diagram + who it's for / its strength / its honest
// cost, with no card, name or CTA around it. An engine's setup screen teaches the method with it
// (owner, 2026-08-23: "use this placeholder/empty state to also educate the user about this
// budget method"). Moved out of the retired first-run intro (BudgetLensIntro.tsx, replaced by
// FirstRun.tsx on 2026-09-25) — the setup screens still use it.
import type { ReactElement } from 'react'
import { Meter } from '../../../ui'

// ── Diagrams — illustrative only, obviously-round numbers ──────────────────────────────

function DiagramRow({
  label,
  value,
  tone,
  ariaLabel,
}: {
  label: string
  value: number
  tone: 'good' | 'bad' | 'warn' | 'neutral'
  ariaLabel: string
}) {
  return (
    <div className="bgl-intro-diagram__row">
      <span className="bgl-intro-diagram__row-label">{label}</span>
      <Meter
        variant="linear"
        size="sm"
        value={value}
        tone={tone}
        ariaLabel={ariaLabel}
        className="bgl-intro-diagram__meter"
      />
    </div>
  )
}

/** One pool (Ready to Assign) drained near-empty, feeding fully-funded categories — the
 *  readable idea is "the pool must reach zero". */
function ZeroBasedDiagram() {
  return (
    <div className="bgl-intro-diagram">
      <DiagramRow
        label="To Assign"
        value={4}
        tone="neutral"
        ariaLabel="Ready to Assign pool, drained down to zero"
      />
      <div className="bgl-intro-diagram__divider" aria-hidden="true" />
      <DiagramRow label="Rent" value={100} tone="good" ariaLabel="Rent category, fully assigned" />
      <DiagramRow label="Food" value={100} tone="good" ariaLabel="Food category, fully assigned" />
      <DiagramRow
        label="Save"
        value={90}
        tone="good"
        ariaLabel="Savings category, fully assigned"
      />
    </div>
  )
}

/** One bar split by proportion — the idea is "one income, three shares". */
function ProportionalDiagram() {
  return (
    <div className="bgl-intro-diagram bgl-intro-diagram--proportional">
      <Meter
        variant="linear"
        size="sm"
        segments={[
          { id: 'needs', pct: 50, className: 'bgl-seg-needs', ariaLabel: 'Needs, 50% of income' },
          { id: 'wants', pct: 30, className: 'bgl-seg-wants', ariaLabel: 'Wants, 30% of income' },
          {
            id: 'savings',
            pct: 20,
            className: 'bgl-seg-savings-pos',
            ariaLabel: 'Savings, 20% of income',
          },
        ]}
        ariaLabel="One income, split 50% needs, 30% wants, 20% savings"
        valueText="50% needs, 30% wants, 20% savings"
      />
      <div className="bgl-hero__legend bgl-intro-diagram__legend">
        {[
          { id: 'needs', label: 'Needs', dotClass: 'bgl-seg-needs' },
          { id: 'wants', label: 'Wants', dotClass: 'bgl-seg-wants' },
          { id: 'savings', label: 'Savings', dotClass: 'bgl-seg-savings-pos' },
        ].map((l) => (
          <div key={l.id} className="bgl-hero__legend-item">
            <span className={`bgl-hero__dot ${l.dotClass}`} aria-hidden="true" />
            <span className="bgl-hero__legend-label">{l.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

const DIAGRAM_BY_ID: Record<string, () => ReactElement> = {
  zero_based: ZeroBasedDiagram,
  proportional: ProportionalDiagram,
}

interface Guidance {
  forWhom: string
  strength: string
  cost: string
}

const GUIDANCE: Record<string, Guidance> = {
  zero_based: {
    forWhom: 'You want every dollar accounted for and will maintain it.',
    strength: 'Nothing is unplanned — overspending is visible immediately and must be resolved.',
    cost: 'Highest upkeep; an overspend forces you to move money from somewhere else.',
  },
  proportional: {
    forWhom: 'You want to know if your spending shape is healthy without managing categories.',
    strength: 'Almost no upkeep; tells you immediately if proportions have drifted.',
    cost: 'No per-category control — it will not stop you overspending, only report it afterwards.',
  },
}

/** The method's diagram + its for/strength/cost, with NO card, name or CTA around it — the
 *  explanatory half of an option card, on its own.
 *
 *  Exported so an engine's empty/setup state can teach the method instead of just naming it
 *  (owner, 2026-08-23: "use this placeholder/empty state to also educate the user about this
 *  budget method"). That screen is the single best teaching moment in the whole feature — the
 *  user has chosen to look at a method and there is nothing else on the page competing for
 *  attention — and reusing this means the setup screen can never drift from the comparison. */
export function MethodExplainer({ engineId }: { engineId: string }) {
  const Diagram = DIAGRAM_BY_ID[engineId]
  const guidance = GUIDANCE[engineId]
  if (!Diagram && !guidance) return null
  // Its OWN fixed-label guidance grid, never a `subgrid`: out here there is no ancestor grid to
  // inherit row tracks from, and a subgrid with nothing to inherit lays itself out arbitrarily —
  // which is exactly what went ragged.
  return (
    <div className="bgl-explainer">
      {Diagram && (
        <div className="bgl-explainer__diagram">
          <Diagram />
        </div>
      )}
      {guidance && (
        <dl className="bgl-explainer__guidance">
          <div className="bgl-explainer__row">
            <dt>For</dt>
            <dd>{guidance.forWhom}</dd>
          </div>
          <div className="bgl-explainer__row">
            <dt>Strength</dt>
            <dd>{guidance.strength}</dd>
          </div>
          <div className="bgl-explainer__row">
            <dt>Honest cost</dt>
            <dd>{guidance.cost}</dd>
          </div>
        </dl>
      )}
    </div>
  )
}
