// accounts/retiredPaymentEnvelope.ts — the disclosure copy for the one DESTRUCTIVE side effect
// an account edit can have. Split out of useAccountsLedger (house pattern: budgetOutside,
// budgetMove) so the wording stays unit-testable without the hook.
//
// Changing an account's `type` away from `credit` retires that card's Credit Card Payment
// envelope and zeroes its assigned amount in EVERY month — immediately, since the account PATCH
// choke re-runs `sync_card_payment_envelopes` on a type flip. Flipping back mints a NEW envelope
// rather than reviving that one, so the money and the history do not come back. The server
// reports what it zeroed; this turns that into one sentence a user can act on.

import { fBase } from '../../../utils/format'

/** One retirement, as `PUT /api/accounts/{id}` reports it under `retired_payment_envelopes`. */
export interface RetiredPaymentEnvelope {
  envelope_id: string
  name: string
  /** `{month: amount}` for every month that had money — the full list, not a count. */
  zeroed_assignments: Record<string, number>
  zeroed_total: number
}

/** The toast for one or more retirements. Names the envelope and the total when there is only
 *  one (the only shape a single account edit can produce); falls back to a count for a batch. */
export function retiredEnvelopeNotice(retired: RetiredPaymentEnvelope[]): string {
  const months = (r: RetiredPaymentEnvelope) => Object.keys(r.zeroed_assignments).length
  if (retired.length === 1) {
    const r = retired[0]!
    const n = months(r)
    const label = r.name ? `“${r.name}”` : 'the card’s payment envelope'
    return (
      `Retired ${label} — ${fBase(r.zeroed_total)} assigned across ` +
      `${n} month${n === 1 ? '' : 's'} was released. Changing the type back creates a new ` +
      `envelope; this one does not come back.`
    )
  }
  const total = retired.reduce((s, r) => s + r.zeroed_total, 0)
  return (
    `Retired ${retired.length} payment envelopes — ${fBase(total)} assigned was released. ` +
    `Changing the type back creates new envelopes; these do not come back.`
  )
}
