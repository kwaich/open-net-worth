// The status line of a card's payment detail (budgetCardPayment.tsx) — reads only the row's own
// server fields (owed / available_for_payment / underfunded); the one derived figure is the
// display-only gap between the balance and what's set aside.
import * as PD from './planData'
import type { LocalCat } from './useBudgetModel'

const fMoney = (n: number) => PD.fBaseCents(n)

export type Status = {
  tone: 'warn' | 'good' | 'muted'
  title: string
  text: string
  /** What assigning would cover the whole balance — 0 when nothing is short. */
  toAssign: number
  /** Paid past what was set aside: the smaller fix that only brings the row back to zero. */
  clearRed?: number
  /** Plain-language reason for the shortfall (owner, 2026-09-28: "we should have the
   * explanation", after YNAB's "Why is my payment underfunded?"). */
  why?: string
}

export function cardPaymentStatus(c: LocalCat): Status {
  const owed = c.paymentOwed ?? 0
  const avail = c.availableForPayment ?? 0
  const under = c.paymentUnderfunded ?? 0
  const gap = Math.max(0, owed - avail)
  const toAssign = Math.round(gap * 100) / 100
  if (avail < -0.005)
    return {
      tone: 'warn',
      title: 'Paid more than set aside',
      // Both figures, and how they add up (owner, 2026-09-28: a small overpayment beside a much
      // larger "more needed" read as a contradiction).
      text:
        `Your payments went ${fMoney(-avail)} past what was set aside, and the card still owes ` +
        `${fMoney(owed)}. ${fMoney(toAssign)} covers both.`,
      toAssign,
      clearRed: Math.round(-avail * 100) / 100,
      why:
        `Usually these payments cleared debt from before your budget started. The cash is gone, ` +
        `but Ready to Assign still counts it. Cover it now, or next month's Ready to Assign ` +
        `starts ${fMoney(-avail)} lower.`,
    }
  if (under > 0.005)
    return {
      tone: 'warn',
      title: 'Underfunded',
      text: `${fMoney(under)} more needed for this bill.`,
      toAssign: Math.round(under * 100) / 100,
      why:
        `Usually it's debt from before your budget started, so nothing was ever set aside for ` +
        `it. Your spending categories are fine.`,
    }
  if (owed <= 0.005)
    return {
      tone: 'muted',
      title: 'Nothing owed',
      text: 'This card has no balance to pay.',
      toAssign: 0,
    }
  if (gap > 0.005)
    return {
      tone: 'good',
      title: 'Spending covered',
      text: `What you've spent is set aside; ${fMoney(gap)} of the balance isn't yet.`,
      toAssign,
    }
  return {
    tone: 'good',
    title: 'Funded',
    text: 'Enough is set aside to pay the full balance.',
    toAssign: 0,
  }
}
