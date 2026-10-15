// A card's payment detail in the side panel (owner, 2026-09-25) — the status line reads only
// the row's own server fields (owed / available_for_payment / underfunded).
import { describe, it, expect } from 'vitest'
import { cardPaymentStatus } from './cardPaymentStatus'
import type { LocalCat } from './useBudgetModel'

const card = (o: Partial<LocalCat>) =>
  ({ key: 'env-cc', label: 'Citi Rewards', isPaymentEnvelope: true, ...o }) as LocalCat

describe('cardPaymentStatus', () => {
  it('underfunded says how much more this bill needs', () => {
    const s = cardPaymentStatus(
      card({ paymentOwed: 500, availableForPayment: 100, paymentUnderfunded: 40 }),
    )
    expect(s.tone).toBe('warn')
    expect(s.title).toBe('Underfunded')
    expect(s.text).toContain('more needed for this bill')
  })
  it('spending covered but the balance not fully set aside', () => {
    const s = cardPaymentStatus(
      card({ paymentOwed: 5351.52, availableForPayment: 5.5, paymentUnderfunded: 0 }),
    )
    expect(s.title).toBe('Spending covered')
  })
  it('the whole balance set aside → Funded; nothing owed → Nothing owed', () => {
    expect(
      cardPaymentStatus(card({ paymentOwed: 200, availableForPayment: 200, paymentUnderfunded: 0 }))
        .title,
    ).toBe('Funded')
    expect(
      cardPaymentStatus(card({ paymentOwed: 0, availableForPayment: 0, paymentUnderfunded: 0 }))
        .title,
    ).toBe('Nothing owed')
  })
  it('underfunded offers to assign the shortfall and explains why', () => {
    const s = cardPaymentStatus(
      card({ paymentOwed: 2000, availableForPayment: 800, paymentUnderfunded: 1200 }),
    )
    expect(s.toAssign).toBe(1200)
    expect(s.why).toContain('before your budget started')
  })
  it('paid past what was set aside: covering it means the whole balance plus the hole', () => {
    // Owes 1,300, payment row at -100 → 1,400 covers both; 100 only clears the red.
    const s = cardPaymentStatus(
      card({ paymentOwed: 1300, availableForPayment: -100, paymentUnderfunded: 1300 }),
    )
    expect(s.title).toBe('Paid more than set aside')
    expect(s.toAssign).toBe(1400)
    expect(s.clearRed).toBe(100)
    expect(s.text).toContain('covers both')
    expect(s.why).toContain('next month')
  })
  it('funded or nothing owed offers nothing to assign', () => {
    expect(
      cardPaymentStatus(card({ paymentOwed: 200, availableForPayment: 200, paymentUnderfunded: 0 }))
        .toAssign,
    ).toBe(0)
  })
})
