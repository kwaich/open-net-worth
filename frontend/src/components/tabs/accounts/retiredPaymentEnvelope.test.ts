// The disclosure copy for a destructive account edit. Review 2026-09-10: retiring a card's
// payment envelope on a type flip was immediate AND silent — the assignment history was gone
// with nothing said, and flipping the type back mints a new envelope rather than reviving it.
import { describe, it, expect } from 'vitest'
import { retiredEnvelopeNotice } from './retiredPaymentEnvelope'
import { setBaseCurrency } from '../../../utils/format'

setBaseCurrency('SGD')

describe('retiredEnvelopeNotice', () => {
  it('names the envelope, the amount and the month count, and says it does not come back', () => {
    const msg = retiredEnvelopeNotice([
      {
        envelope_id: 'env_1',
        name: 'Citi Rewards',
        zeroed_assignments: { '2026-06': 200, '2026-07': 150.5 },
        zeroed_total: 350.5,
      },
    ])
    expect(msg).toContain('Citi Rewards')
    expect(msg).toContain('350.5')
    expect(msg).toContain('2 months')
    expect(msg).toContain('does not come back')
  })

  it('says "1 month" for a single month', () => {
    const msg = retiredEnvelopeNotice([
      {
        envelope_id: 'env_1',
        name: 'Amex',
        zeroed_assignments: { '2026-06': 40 },
        zeroed_total: 40,
      },
    ])
    expect(msg).toContain('1 month')
    expect(msg).not.toContain('1 months')
  })

  it('falls back to a count and a combined total for a batch', () => {
    const msg = retiredEnvelopeNotice([
      { envelope_id: 'a', name: 'A', zeroed_assignments: { '2026-06': 10 }, zeroed_total: 10 },
      { envelope_id: 'b', name: 'B', zeroed_assignments: { '2026-06': 20 }, zeroed_total: 20 },
    ])
    expect(msg).toContain('2 payment envelopes')
    expect(msg).toContain('30')
  })

  it('still reads sensibly when the envelope carries no name', () => {
    const msg = retiredEnvelopeNotice([
      { envelope_id: 'a', name: '', zeroed_assignments: { '2026-06': 10 }, zeroed_total: 10 },
    ])
    expect(msg).toContain('payment envelope')
  })
})
