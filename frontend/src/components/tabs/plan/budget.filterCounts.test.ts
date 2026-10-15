// FEB-05 (hidden-row policy) — pure-function coverage for the two helpers budget.tsx's
// BudgetTab derives its Summary/toolbar figures from.
import { describe, it, expect } from 'vitest'
import { buildFilterCounts, buildStatusTotals, buildCardDebtUnderfunded } from './budget'
import type { LocalCat } from './useBudgetModel'

function mkCat(
  overrides: Partial<LocalCat> & Pick<LocalCat, 'key' | 'label' | 'envelopeId' | 'categoryIds'>,
): LocalCat {
  return {
    color: '#34b08a',
    assigned: 0,
    activity: 0,
    available: 0,
    target: 0,
    tt: null,
    cad: null,
    status: 'none',
    needed: 0,
    ...overrides,
  }
}

describe('buildFilterCounts — FEB-05: ONE hidden-row policy for every toolbar/chip count', () => {
  const underfunded = mkCat({
    key: 'groceries',
    label: 'Groceries',
    envelopeId: 'env-groceries',
    categoryIds: ['groceries'],
    status: 'underfunded',
  })
  const overspent = mkCat({
    key: 'fun',
    label: 'Fun',
    envelopeId: 'env-fun',
    categoryIds: ['fun'],
    status: 'overspent',
  })
  const hiddenUnderfunded = mkCat({
    key: 'travel',
    label: 'Travel',
    envelopeId: 'env-travel',
    categoryIds: ['travel'],
    status: 'underfunded',
    hidden: true,
  })

  it('a hidden row is excluded from EVERY count, not just "all"', () => {
    const counts = buildFilterCounts([underfunded, overspent, hiddenUnderfunded])
    expect(counts.all).toBe(2)
    expect(counts.underfunded).toBe(1) // the hidden underfunded row does NOT count
  })

  it('counts every status bucket independently', () => {
    const counts = buildFilterCounts([underfunded, overspent])
    expect(counts).toMatchObject({ all: 2, underfunded: 1, overspent: 1, overfunded: 0 })
  })

  it('"funded" counts any row with available > 0.005', () => {
    const funded = mkCat({
      key: 'dining',
      label: 'Dining',
      envelopeId: 'env-dining',
      categoryIds: ['dining'],
      available: 40,
    })
    const counts = buildFilterCounts([funded])
    expect(counts.funded).toBe(1)
  })
})

describe('buildStatusTotals — FEB-05: the Summary/RTA-popover/AutoAssign counts, hidden-exclusive', () => {
  it('sums the overspent deficit and underfunded shortfall across VISIBLE rows only', () => {
    const overspent = mkCat({
      key: 'fun',
      label: 'Fun',
      envelopeId: 'env-fun',
      categoryIds: ['fun'],
      status: 'overspent',
      available: -25,
    })
    const underfunded = mkCat({
      key: 'groceries',
      label: 'Groceries',
      envelopeId: 'env-groceries',
      categoryIds: ['groceries'],
      status: 'underfunded',
      needed: 50,
    })
    const hiddenOverspent = mkCat({
      key: 'ghost',
      label: 'Ghost',
      envelopeId: 'env-ghost',
      categoryIds: ['ghost'],
      status: 'overspent',
      available: -999,
      hidden: true,
    })
    const totals = buildStatusTotals([overspent, underfunded, hiddenOverspent])
    expect(totals).toEqual({
      overspentCount: 1,
      overspentAmount: 25,
      underfundedCount: 1,
      underfundedAmount: 50,
    })
  })

  it('an all-hidden book reports zero everywhere (never counts a row nobody can see)', () => {
    const hidden = mkCat({
      key: 'ghost',
      label: 'Ghost',
      envelopeId: 'env-ghost',
      categoryIds: ['ghost'],
      status: 'overspent',
      available: -50,
      hidden: true,
    })
    expect(buildStatusTotals([hidden])).toEqual({
      overspentCount: 0,
      overspentAmount: 0,
      underfundedCount: 0,
      underfundedAmount: 0,
    })
  })
})

// A CASH-OVERPAID payment envelope counts as Overspent (verified against YNAB: a card payment
// row at Activity −$64.50 / Available −$64.50 produces YNAB's "1 Overspent" badge; we reported
// 0). The GOLD case — a card that spent more via purchases than its envelope funds — is card
// debt, not a cash hole, and stays out of these counts (buildCardDebtUnderfunded owns it).
describe('overspent counts include a cash-overpaid payment envelope, never an underfunded one', () => {
  const mkCard = (overrides: Partial<LocalCat>) =>
    mkCat({
      key: 'env-visa',
      label: 'Visa Payment',
      envelopeId: 'env-visa',
      categoryIds: [],
      isPaymentEnvelope: true,
      // mapCat forces every payment row's status to 'none' — the count must not lean on it.
      status: 'none',
      ...overrides,
    })
  // Paid $64.50 more onto the card than the envelope had set aside.
  const overpaid = mkCard({ available: -64.5, availableForPayment: -64.5 })
  // Spent more on the card than the envelope funds: gold, not red — availableForPayment >= 0.
  const cardDebt = mkCard({ available: 0, availableForPayment: 0, paymentUnderfunded: 120 })
  const ordinaryOverspent = mkCat({
    key: 'fun',
    label: 'Fun',
    envelopeId: 'env-fun',
    categoryIds: ['fun'],
    status: 'overspent',
    available: -25,
  })

  it('buildStatusTotals counts the overpaid card and its dollar amount', () => {
    expect(buildStatusTotals([overpaid])).toEqual({
      overspentCount: 1,
      overspentAmount: 64.5,
      underfundedCount: 0,
      underfundedAmount: 0,
    })
  })

  it('buildFilterCounts counts the overpaid card in the Overspent chip', () => {
    const counts = buildFilterCounts([overpaid])
    expect(counts.overspent).toBe(1)
    // ...and in no OTHER chip: the target vocabulary still has no meaning for a payment row.
    expect(counts).toMatchObject({ all: 0, underfunded: 0, overfunded: 0, funded: 0 })
  })

  it('an overpaid card sums TOGETHER with ordinary overspent rows (the banner total)', () => {
    const totals = buildStatusTotals([ordinaryOverspent, overpaid])
    expect(totals.overspentCount).toBe(2)
    expect(totals.overspentAmount).toBeCloseTo(89.5, 2)
    expect(buildFilterCounts([ordinaryOverspent, overpaid]).overspent).toBe(2)
  })

  it('an UNDERFUNDED (gold) card is NOT overspent — the two cases stay distinct', () => {
    expect(buildStatusTotals([cardDebt])).toEqual({
      overspentCount: 0,
      overspentAmount: 0,
      underfundedCount: 0,
      underfundedAmount: 0,
    })
    expect(buildFilterCounts([cardDebt]).overspent).toBe(0)
    expect(buildCardDebtUnderfunded([cardDebt])).toEqual({ count: 1, amount: 120 })
  })

  it('a card with neither hole counts nowhere, and sub-cent dust is not a hole', () => {
    const funded = mkCard({ key: 'env-amex', available: 200, availableForPayment: 200 })
    const dust = mkCard({ key: 'env-mc', available: -0.001, availableForPayment: -0.001 })
    expect(buildStatusTotals([funded, dust]).overspentCount).toBe(0)
    expect(buildFilterCounts([funded, dust]).overspent).toBe(0)
    // A funded card's positive available must still never inflate "Money available".
    expect(buildFilterCounts([funded, dust]).funded).toBe(0)
  })

  it('a HIDDEN overpaid card still counts nowhere (FEB-05 hidden-row policy holds)', () => {
    const hiddenOverpaid = mkCard({ available: -64.5, availableForPayment: -64.5, hidden: true })
    expect(buildStatusTotals([hiddenOverpaid]).overspentCount).toBe(0)
    expect(buildFilterCounts([hiddenOverpaid]).overspent).toBe(0)
  })
})

// feat/budget-summary-rail — the desktop rail's "Underfunded" (card debt) stat sums a card's own
// payment envelope going underfunded, a different bucket from buildStatusTotals' target-based
// underfunded (payment envelopes carry no target at all, UI spec §6).
describe("buildCardDebtUnderfunded — the rail Auto-assign panel's card-debt stat", () => {
  it('sums underfunded payment envelopes only, excluding ordinary underfunded envelopes', () => {
    const card = mkCat({
      key: 'env-visa',
      label: 'Visa Payment',
      envelopeId: 'env-visa',
      categoryIds: [],
      isPaymentEnvelope: true,
      paymentUnderfunded: 42.5,
    })
    const ordinary = mkCat({
      key: 'groceries',
      label: 'Groceries',
      envelopeId: 'env-groceries',
      categoryIds: ['groceries'],
      status: 'underfunded',
      needed: 50,
    })
    expect(buildCardDebtUnderfunded([card, ordinary])).toEqual({ count: 1, amount: 42.5 })
  })

  it('excludes a hidden payment envelope', () => {
    const card = mkCat({
      key: 'env-visa',
      label: 'Visa Payment',
      envelopeId: 'env-visa',
      categoryIds: [],
      isPaymentEnvelope: true,
      paymentUnderfunded: 42.5,
      hidden: true,
    })
    expect(buildCardDebtUnderfunded([card])).toEqual({ count: 0, amount: 0 })
  })

  it('sums across multiple underfunded cards', () => {
    const visa = mkCat({
      key: 'env-visa',
      label: 'Visa Payment',
      envelopeId: 'env-visa',
      categoryIds: [],
      isPaymentEnvelope: true,
      paymentUnderfunded: 42.5,
    })
    const amex = mkCat({
      key: 'env-amex',
      label: 'Amex Payment',
      envelopeId: 'env-amex',
      categoryIds: [],
      isPaymentEnvelope: true,
      paymentUnderfunded: 17.25,
    })
    expect(buildCardDebtUnderfunded([visa, amex])).toEqual({ count: 2, amount: 59.75 })
  })

  it('is zero when no card is underfunded, and never a sub-cent false positive', () => {
    const funded = mkCat({
      key: 'env-visa',
      label: 'Visa Payment',
      envelopeId: 'env-visa',
      categoryIds: [],
      isPaymentEnvelope: true,
      paymentUnderfunded: 0,
    })
    const dust = mkCat({
      key: 'env-amex',
      label: 'Amex Payment',
      envelopeId: 'env-amex',
      categoryIds: [],
      isPaymentEnvelope: true,
      paymentUnderfunded: 0.001,
    })
    expect(buildCardDebtUnderfunded([funded, dust])).toEqual({ count: 0, amount: 0 })
  })
})
