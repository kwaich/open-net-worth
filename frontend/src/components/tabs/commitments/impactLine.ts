// Pure "not happening" impact-line text builder (detail3.jsx's `Actions` component's inline
// JSX, ported to a plain-text pure function so it's unit-testable) — the plain-language
// consequence shown when an action is marked as not going to happen, keyed off its ROLE
// (inflow/outflow/prep, see utils/commitments.ts's `actionRole`).
import type { CommitmentAction } from '../../../types/commitments'
import { actionRole, daysUntil, fmtDay } from '../../../utils/commitments'
import { fBase, fCurrency } from '../../../utils/format'

export interface ImpactContext {
  commitmentName: string
  action: CommitmentAction
  /** The base value of the funding source this action unlocks (action.funding_id), when it
   *  resolves to a real, still-live source — null when the action carries no funding_id, or
   *  the source can't be found. */
  fundingAmountBase: number | null
}

function leadTime(days: number): string {
  if (days >= 60) return `${Math.round(days / 30)} months`
  if (days >= 25) return `${Math.round(days / 7)} weeks`
  return `${days} day${days === 1 ? '' : 's'}`
}

/** Builds the plain-language consequence sentence for an action marked "not happening". */
export function buildImpactLine({
  commitmentName,
  action,
  fundingAmountBase,
}: ImpactContext): string {
  const role = actionRole(action)
  const dLeft = action.due_date ? daysUntil(action.due_date) : null
  const ahead = dLeft != null && dLeft > 0

  if (role === 'inflow') {
    if (fundingAmountBase != null) {
      const leadSuffix = ahead ? ` (${leadTime(dLeft as number)} before it was due)` : ''
      return `${fBase(fundingAmountBase)} won't come in — pulled from ${commitmentName}'s funding${leadSuffix}.`
    }
    const amtNative = action.amount != null ? fCurrency(action.amount, action.currency) : null
    const amtSuffix = amtNative ? ` of ${amtNative}` : ''
    const whenSuffix = ahead && action.due_date ? ` around ${fmtDay(action.due_date)}` : ''
    return `This inflow${amtSuffix} won't arrive — ${commitmentName}'s cushion loses it${whenSuffix}.`
  }
  if (role === 'outflow') {
    return `This payment won't go out — the installment it covers on ${commitmentName} would fall into arrears.`
  }
  return `This prerequisite won't be done — the ${commitmentName} steps that depend on it are now at risk.`
}

/** The short PRE-decision hint next to the "Not happening" button (before it's clicked) —
 *  distinct, shorter copy from `buildImpactLine`'s post-decision consequence sentence. */
export function buildDidntHint(action: CommitmentAction, fundingAmountBase: number | null): string {
  const role = actionRole(action)
  if (role === 'inflow' && fundingAmountBase != null) {
    return `pulls ${fBase(fundingAmountBase)} — re-runs the outlook & suggests a fix`
  }
  if (role === 'inflow') return 'this inflow drops — Lucius helps you rebuild it'
  if (role === 'outflow') return 'flags the payment at-risk — Lucius helps you re-plan'
  return 'flags the step at-risk — Lucius helps you re-sequence'
}
