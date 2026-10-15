import { describe, it, expect } from 'vitest'
import { buildClientAutoSteps, mapServerAutoSteps } from './useBudgetModel'
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

describe('buildClientAutoSteps — client auto-assign preview modes (handoff delta #10)', () => {
  const underfunded = mkCat({
    key: 'groceries',
    label: 'Groceries',
    envelopeId: 'env-groceries',
    categoryIds: ['groceries'],
    status: 'underfunded',
    assigned: 100,
    needed: 50,
    color: '#3b86dd',
  })
  const funded = mkCat({
    key: 'dining',
    label: 'Dining',
    envelopeId: 'env-dining',
    categoryIds: ['dining'],
    status: 'funded',
    assigned: 80,
  })
  const overspent = mkCat({
    key: 'fun',
    label: 'Fun',
    envelopeId: 'env-fun',
    categoryIds: ['fun'],
    status: 'overspent',
    assigned: 10,
    available: -25,
  })
  const hiddenUnderfunded = mkCat({
    key: 'travel',
    label: 'Travel',
    envelopeId: 'env-travel',
    categoryIds: ['travel'],
    status: 'underfunded',
    assigned: 0,
    needed: 200,
    hidden: true,
  })

  it("Underfunded funds the targets, then each card's payment shortfall (YNAB, 2026-09-28)", () => {
    const card = mkCat({
      key: 'env-visa',
      label: 'Visa',
      envelopeId: 'env-visa',
      categoryIds: [],
      isPaymentEnvelope: true,
      assigned: 20,
      paymentUnderfunded: 300,
    })
    const paidOff = mkCat({
      key: 'env-amex',
      label: 'Amex',
      envelopeId: 'env-amex',
      categoryIds: [],
      isPaymentEnvelope: true,
      paymentUnderfunded: 0,
    })
    const steps = buildClientAutoSteps([card, paidOff, underfunded], 'Underfunded')
    expect(steps.map((s) => [s.key, s.next, s.delta])).toEqual([
      ['groceries', 150, 50],
      ['env-visa', 320, 300],
    ])
    expect(buildClientAutoSteps([card], 'Reset Assigned Amounts')).toEqual([])
  })

  it('"Underfunded" steps ONLY underfunded rows, next = assigned + needed', () => {
    const steps = buildClientAutoSteps([underfunded, funded, overspent], 'Underfunded')
    expect(steps).toHaveLength(1)
    expect(steps[0]).toEqual({
      key: 'groceries',
      label: 'Groceries',
      color: '#3b86dd',
      cur: 100,
      next: 150,
      delta: 50,
    })
  })

  it('"Top up overspent envelopes" steps ONLY overspent rows, next = assigned + (-available)', () => {
    const steps = buildClientAutoSteps(
      [underfunded, funded, overspent],
      'Top up overspent envelopes',
    )
    expect(steps).toHaveLength(1)
    expect(steps[0]).toMatchObject({ key: 'fun', cur: 10, next: 35, delta: 25 })
  })

  it('"Reset Assigned Amounts" steps every row with a non-zero assigned, next always 0', () => {
    const steps = buildClientAutoSteps([underfunded, funded, overspent], 'Reset Assigned Amounts')
    expect(steps.map((s) => s.key).sort()).toEqual(['dining', 'fun', 'groceries'])
    expect(steps.every((s) => s.next === 0)).toBe(true)
    expect(steps.find((s) => s.key === 'groceries')!.delta).toBe(-100)
  })

  it('excludes hidden rows from every client mode', () => {
    const steps = buildClientAutoSteps([hiddenUnderfunded], 'Underfunded')
    expect(steps).toEqual([])
  })

  it('an unknown label yields no steps (history modes are handled elsewhere)', () => {
    expect(buildClientAutoSteps([underfunded], 'Assigned last month')).toEqual([])
  })

  it('a $0 delta never becomes a step (e.g. reset on an already-zero row)', () => {
    const zero = mkCat({
      key: 'zero',
      label: 'Zero',
      envelopeId: 'env-zero',
      categoryIds: ['zero'],
      assigned: 0,
    })
    expect(buildClientAutoSteps([zero], 'Reset Assigned Amounts')).toEqual([])
  })
})

describe('mapServerAutoSteps — history modes (assigned/spent last month, average)', () => {
  const colorOf = (key: string) => ({ groceries: '#3b86dd', dining: '#e0a032' })[key] ?? '#34b08a'
  const noneHidden = () => false

  it('maps envelope_id→key, current_assigned→cur, new_assigned→next, resolves color from the model', () => {
    const steps = mapServerAutoSteps(
      [
        {
          envelope_id: 'groceries',
          label: 'Groceries',
          current_assigned: 100,
          new_assigned: 130,
          delta: 30,
        },
      ],
      colorOf,
      noneHidden,
    )
    expect(steps).toEqual([
      { key: 'groceries', label: 'Groceries', color: '#3b86dd', cur: 100, next: 130, delta: 30 },
    ])
  })

  it('falls back to a stable color for a row not found in the live model', () => {
    const steps = mapServerAutoSteps(
      [
        {
          envelope_id: 'unknown_cat',
          label: 'Unknown',
          current_assigned: 0,
          new_assigned: 10,
          delta: 10,
        },
      ],
      colorOf,
      noneHidden,
    )
    expect(steps[0]!.color).toBe('#34b08a')
  })

  it('filters out $0-delta steps (a row whose history amount matches its current assigned)', () => {
    const steps = mapServerAutoSteps(
      [
        {
          envelope_id: 'groceries',
          label: 'Groceries',
          current_assigned: 100,
          new_assigned: 100,
          delta: 0,
        },
        {
          envelope_id: 'dining',
          label: 'Dining',
          current_assigned: 50,
          new_assigned: 80,
          delta: 30,
        },
      ],
      colorOf,
      noneHidden,
    )
    expect(steps.map((s) => s.key)).toEqual(['dining'])
  })

  // FEB-05 — hidden-row policy parity: a history mode must exclude a hidden row exactly like
  // the client modes already do (buildClientAutoSteps' own "excludes hidden rows from every
  // client mode" test above), so "Fund N underfunded targets" can never disagree with what a
  // history-mode auto-assign actually writes.
  it('excludes a hidden row even though the server itself has no hidden-row concept', () => {
    const steps = mapServerAutoSteps(
      [
        {
          envelope_id: 'groceries',
          label: 'Groceries',
          current_assigned: 100,
          new_assigned: 130,
          delta: 30,
        },
        {
          envelope_id: 'travel',
          label: 'Travel',
          current_assigned: 0,
          new_assigned: 200,
          delta: 200,
        },
      ],
      colorOf,
      (key) => key === 'travel',
    )
    expect(steps.map((s) => s.key)).toEqual(['groceries'])
  })
})
