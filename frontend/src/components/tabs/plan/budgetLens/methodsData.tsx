// budgetLens/methodsData.tsx — the budgeting methods Plan › Budget offers, ported from the
// Budget Onboarding (cards) design (budget_onboarding/diagrams.jsx, owner 2026-09-25). Every
// string is one line on purpose: the diagram IS the comparison. There are exactly two — in the
// design's own order.
import type { ReactNode } from 'react'

export type MethodId = 'proportional' | 'zero_based'
export type FitId = 'shape' | 'every'

export interface MethodMeta {
  id: MethodId
  name: string
  hook: ReactNode
  effort: 1 | 2 | 3
  /** What happens when you overspend: reported at month end, or it must be covered. */
  stops: 'reports' | 'must-resolve'
  /** Does leftover money stay put? `null` — the method doesn't track it at all. */
  carry: boolean | null
  fit: FitId
  forWhom: string
  cost: string
}

export const METHODS: MethodMeta[] = [
  {
    id: 'proportional',
    name: '50/30/20',
    hook: (
      <>
        Income split into <b>Needs, Wants, Savings</b>.
      </>
    ),
    effort: 1,
    stops: 'reports',
    carry: null,
    fit: 'shape',
    forWhom: 'You want a quick health check without managing categories.',
    cost: 'Glance at the split once a month. Nothing to maintain.',
  },
  {
    id: 'zero_based',
    name: 'Zero-Based',
    hook: (
      <>
        Every dollar gets a job. <b>Assign to zero.</b>
      </>
    ),
    effort: 3,
    stops: 'must-resolve',
    carry: true,
    fit: 'every',
    forWhom: 'You want every dollar accounted for and will keep it up.',
    cost: 'Assign each paycheck, then check in weekly and move money when a category runs over.',
  },
]

export const FITS: { id: FitId; label: string; color: string }[] = [
  { id: 'shape', label: 'Just check my shape', color: '#3b86dd' },
  { id: 'every', label: 'Account for every dollar', color: '#34b08a' },
]

export const methodById = (id: string | null | undefined): MethodMeta | undefined =>
  METHODS.find((m) => m.id === id)

export const fitColor = (fit: FitId): string =>
  FITS.find((f) => f.id === fit)?.color ?? 'var(--primary)'
