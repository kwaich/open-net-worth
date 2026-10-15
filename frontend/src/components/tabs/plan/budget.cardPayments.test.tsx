// @vitest-environment jsdom
//
// Credit Card Payments payment-envelope rows (YNAB-engine credit_card_mode="payment_envelope",
// ADR 2026-07-09-ynab-engine-credit-card-payment-envelopes.md) — the frontend rendering half
// (UI spec: docs/qa/acceptance/credit-card-payments-ui.md). The backend emits, on a payment
// row only, `is_payment_envelope: true` + raw signed `available_for_payment` + `underfunded`;
// every new LocalCat field defaults falsy/zero (mapCat in useBudgetModel.ts), so an ordinary
// row (the pre-existing signed-mode shape) must render byte-identical to before this change.
// Mirrors budget.zeroEnvelope.test.tsx's render-CategoryRow-directly convention.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { CategoryRow, buildFilterCounts, buildStatusTotals } from './budget'
import { fSmart, fBase } from './planData'
import { mapCat, buildClientAutoSteps } from './useBudgetModel'
import type { LocalCat } from './useBudgetModel'
import type { BudgetCategoryRow } from '../../../hooks/useBudget'

afterEach(() => cleanup())

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

function noopDnd() {
  return {
    catStart: () => () => {},
    groupStart: () => () => {},
    rowOver: () => () => {},
    rowDrop: () => () => {},
    groupOver: () => () => {},
    groupDrop: () => () => {},
    end: () => {},
  }
}

function renderRow(
  c: LocalCat,
  opts: {
    locked?: boolean
    onOpenCommitment?: (c: LocalCat) => void
    onOpenCardPayment?: (c: LocalCat) => void
  } = {},
) {
  const onOpenCommitment = opts.onOpenCommitment ?? vi.fn()
  const onOpenCardPayment = opts.onOpenCardPayment ?? vi.fn()
  const utils = render(
    <CategoryRow
      c={c}
      gid="g-a"
      dnd={noopDnd()}
      over={null}
      otherGroups={[]}
      locked={!!opts.locked}
      month="2026-07"
      allGroups={[]}
      catLabel={(k) => k}
      catColor={() => '#34b08a'}
      rta={0}
      onOpenCommitment={onOpenCommitment}
      onOpenCardPayment={onOpenCardPayment}
      onAssign={vi.fn()}
      onAction={vi.fn()}
      onRename={vi.fn()}
      onRecolor={vi.fn()}
      onMoveTo={vi.fn()}
      onToggleHide={vi.fn()}
      onDeleteEnvelope={vi.fn()}
      onEditTarget={vi.fn()}
      onTransfer={vi.fn()}
      onMerge={vi.fn()}
      onSplit={vi.fn()}
      onAddCats={vi.fn()}
    />,
  )
  return { ...utils, onOpenCommitment, onOpenCardPayment }
}

// A bot-managed payment envelope row, like the real server shape: zero-category, locked.
function mkPaymentCat(
  overrides: Partial<LocalCat> & Pick<LocalCat, 'key' | 'label' | 'envelopeId'>,
): LocalCat {
  return mkCat({ categoryIds: [], isPaymentEnvelope: true, ...overrides })
}

describe('CategoryRow — signed mode is byte-identical (no payment fields)', () => {
  it('an ordinary row renders no Payment cell, no pl-paycell, and its old status line intact', () => {
    const c = mkCat({
      key: 'dining',
      label: 'Dining',
      envelopeId: 'env-dining',
      categoryIds: ['dining'],
      available: 42,
      status: 'funded',
    })
    const { container } = renderRow(c)
    expect(screen.queryByText('Payment')).toBeNull()
    expect(container.querySelector('.pl-paycell')).toBeNull()
    expect(screen.getByText('Funded')).toBeTruthy()
  })

  it('a locked commitment-style row (not a payment envelope) still routes its click through onOpenCommitment', () => {
    const c = mkCat({ key: 'env-commit', label: 'Rent', envelopeId: 'env-commit', categoryIds: [] })
    const { container, onOpenCommitment, onOpenCardPayment } = renderRow(c, { locked: true })
    fireEvent.click(container.querySelector('.pl-crow')!)
    expect(onOpenCommitment).toHaveBeenCalledWith(c)
    expect(onOpenCardPayment).not.toHaveBeenCalled()
  })
})

describe('CategoryRow — Credit Card Payments states (UI spec §3.2/§8/§9)', () => {
  it('funded: available_for_payment ≥ 0, underfunded ≈ 0 → green "Funded", no tooltip', () => {
    const c = mkPaymentCat({
      key: 'env-cc-chase',
      label: 'Chase Sapphire',
      envelopeId: 'env-cc-chase',
      availableForPayment: 250,
      paymentUnderfunded: 0,
    })
    const { container } = renderRow(c, { locked: true })
    const status = container.querySelector('.pl-cstatus')!
    expect(status.textContent).toBe('Funded')
    expect(status.className).toContain('funded')
    const cell = container.querySelector('.pl-paycell')!
    expect(cell.getAttribute('title')).toBeNull()
    expect(screen.getByText('Payment')).toBeTruthy()
  })

  it('$0 payment envelope (nothing set aside, nothing owed) shows NO status — not green "Funded"', () => {
    // A card with available_for_payment 0 and underfunded 0 is neutral, not "Funded" — "Funded"
    // (green) should mean money is actually set aside, else it's meaningless (owner: 2026-07-10).
    const c = mkPaymentCat({
      key: 'env-cc-x',
      label: 'Meridian Bank',
      envelopeId: 'env-cc-x',
      availableForPayment: 0,
      paymentUnderfunded: 0,
    })
    const { container } = renderRow(c, { locked: true })
    expect(container.querySelector('.pl-cstatus')).toBeNull()
    expect(screen.queryByText('Funded')).toBeNull()
    expect(screen.getByText('Payment')).toBeTruthy() // the Payment cell still renders (shows $0)
  })

  it('underfunded: available_for_payment ≥ 0, underfunded > 0 → amber "{N} more needed for this bill"', () => {
    const c = mkPaymentCat({
      key: 'env-cc-amex',
      label: 'Amex Platinum',
      envelopeId: 'env-cc-amex',
      availableForPayment: 90,
      paymentUnderfunded: 110,
      paymentOwed: 200,
    })
    const { container } = renderRow(c, { locked: true })
    const status = container.querySelector('.pl-cstatus')!
    expect(status.textContent).toBe(`${fSmart(110)} more needed for this bill`)
    expect(status.className).toContain('under')
    const cell = container.querySelector('.pl-paycell')!
    expect(cell.getAttribute('title')).toBe(
      `You have a ${fSmart(200)} bill but only ${fSmart(90)} set aside for Amex Platinum.`,
    )
  })

  it('overpaid: available_for_payment < 0 → NO status text, and it still suppresses underfunded', () => {
    // paymentUnderfunded is also > 0 here on purpose. An overpaid card shows no status label at
    // all (owner ask, 2026-09-15 — paying a card more than you set aside isn't a fault the way
    // an overspent envelope is), but overpaid must still WIN over underfunded: the row must go
    // silent, never fall through to "S$110 more needed for this bill", which would be false.
    // The figure itself is still on the row — the Available cell and the payment cell's title.
    const c = mkPaymentCat({
      key: 'env-cc-prem',
      label: 'Meridian Bank Prestige',
      envelopeId: 'env-cc-prem',
      availableForPayment: -20,
      paymentUnderfunded: 110,
    })
    const { container } = renderRow(c, { locked: true })
    expect(container.querySelector('.pl-cstatus')).toBeNull()
    const cell = container.querySelector('.pl-paycell')!
    expect(cell.getAttribute('title')).toBe(
      'You sent ' + fSmart(20) + ' more to Meridian Bank Prestige than you had set aside.',
    )
  })

  it('prepaid card: underfunded 0 with a negative payment balance still reads as a loss via the figure', () => {
    // The payment row's `underfunded` is max(0, owed − max(available_for_payment, 0)), so paying
    // the card beyond what was set aside reports 0 owed. Nothing is due — but S$150 of cash left
    // without an allotment behind it, and the row must say so rather than go quiet or read Funded.
    const c = mkPaymentCat({
      key: 'env-cc-prepaid',
      label: 'Meridian Bank Prestige',
      envelopeId: 'env-cc-prepaid',
      availableForPayment: -150,
      paymentUnderfunded: 0,
    })
    const { container } = renderRow(c, { locked: true })
    // No status LABEL (owner ask, 2026-09-15) — but the loss is still plainly on the row: the
    // Available cell renders the raw negative figure in red, and the payment cell says it in
    // full on hover. "Never silence" is satisfied by the figure, not by an editorial verdict.
    expect(container.querySelector('.pl-cstatus')).toBeNull()
    expect(container.querySelector('.pl-avail')!.className).toContain('bad')
    expect(container.querySelector('.pl-paycell')!.getAttribute('title')).toBe(
      `You sent ${fSmart(150)} more to Meridian Bank Prestige than you had set aside.`,
    )
  })

  it('the Payment figure renders the raw signed available_for_payment, not the plain (unrelated) available field', () => {
    const c = mkPaymentCat({
      key: 'env-cc-prem',
      label: 'Meridian Bank Prestige',
      envelopeId: 'env-cc-prem',
      available: 999,
      availableForPayment: -20,
      paymentUnderfunded: 0,
    })
    const { container } = renderRow(c, { locked: true })
    // fFig (used by AvailCell) is the TABLE formatter and always prints cents (2026-09-15),
    // where fSmart drops them on a whole amount — so this asserts fBase, not fSmart.
    expect(container.querySelector('.pl-paycell')!.textContent).toBe('Payment' + fBase(-20))
  })
})

describe('CategoryRow — payment row click routing + no target-based machinery leaks through', () => {
  it('a locked payment row routes its click through onOpenCardPayment, not onOpenCommitment', () => {
    const c = mkPaymentCat({
      key: 'env-cc-chase',
      label: 'Chase Sapphire',
      envelopeId: 'env-cc-chase',
      availableForPayment: 250,
      paymentUnderfunded: 0,
    })
    const { container, onOpenCommitment, onOpenCardPayment } = renderRow(c, { locked: true })
    fireEvent.click(container.querySelector('.pl-crow')!)
    expect(onOpenCardPayment).toHaveBeenCalledWith(c)
    expect(onOpenCommitment).not.toHaveBeenCalled()
  })

  it('never renders the target progress bar or Cover/Fund/Move actions, even when status/target fields are set', () => {
    // A payment row's own `status`/`target`/`needed` are never populated server-side, but even
    // if something upstream set them, the isPaymentEnvelope gate must still suppress the
    // target-based UI entirely (UI spec §6).
    const c = mkPaymentCat({
      key: 'env-cc-chase',
      label: 'Chase Sapphire',
      envelopeId: 'env-cc-chase',
      availableForPayment: -20,
      paymentUnderfunded: 0,
      status: 'overspent',
      target: 100,
      needed: 40,
      available: -20,
    })
    const { container } = renderRow(c, { locked: true })
    expect(container.querySelector('.pl-tbar-wrap')).toBeNull()
    expect(container.querySelector('.pl-caction')).toBeNull()
  })
})

// The hint shows the tag glyph + "0"; the noun rides along for screen readers, so its own text
// still reads "0 categories" (owner, 2026-09-25) — match the element, not a text node.
const emptyHint = () =>
  screen.queryByText(
    (_, el) => !!el?.matches('.pl-gcount--empty') && el.textContent === '0 categories',
  )

describe('CategoryRow — pl-gcount--empty hint suppressed on locked rows (in-scope fix)', () => {
  it('a locked zero-category payment row never shows "0 categories"', () => {
    const c = mkPaymentCat({
      key: 'env-cc-chase',
      label: 'Chase Sapphire',
      envelopeId: 'env-cc-chase',
      availableForPayment: 250,
      paymentUnderfunded: 0,
    })
    renderRow(c, { locked: true })
    expect(emptyHint()).toBeNull()
  })

  it('a locked zero-category commitment row never shows "0 categories" either', () => {
    const c = mkCat({ key: 'env-commit', label: 'Rent', envelopeId: 'env-commit', categoryIds: [] })
    renderRow(c, { locked: true })
    expect(emptyHint()).toBeNull()
  })

  it('control: the SAME zero-category shape still shows the hint once UNLOCKED (proves the gate is `locked`)', () => {
    const c = mkCat({
      key: 'env-empty',
      label: 'Emergency fund',
      envelopeId: 'env-empty',
      categoryIds: [],
    })
    renderRow(c, { locked: false })
    expect(emptyHint()).toBeTruthy()
  })
})

// ── Regression: the mapCat adaptation layer + the target-based aggregations must never treat a
// payment envelope as a target row (code-review blocker: an overpaid card's negative `available`
// otherwise maps to status 'overspent' → leaks into the "Top up overspent envelopes" bulk auto-assign
// = a real, unintended money write into a locked card row). These exercise the exact seams the
// CategoryRow-only tests above bypass. ──────────────────────────────────────────────────────────

function mkRow(
  overrides: Partial<BudgetCategoryRow> & Pick<BudgetCategoryRow, 'key' | 'label'>,
): BudgetCategoryRow {
  return {
    envelope_id: overrides.key,
    category_ids: [overrides.key],
    color: '',
    assigned: 0,
    activity: 0,
    available: 0,
    target: null,
    needed_this_month: 0,
    status: 'none',
    ...overrides,
  }
}
const mapRow = (row: BudgetCategoryRow) => mapCat(row, new Map(), new Set(), '2026-07')

describe('mapCat — payment fields map with safe defaults; payment status forced to none', () => {
  it('signed-mode shape (payment fields omitted) → falsy/zero defaults, ordinary status preserved', () => {
    // A genuinely overspent ORDINARY row must still map to status 'overspent' — proves the new
    // gate is payment-specific, not a blanket suppression.
    const lc = mapRow(mkRow({ key: 'dining', label: 'Dining', available: -20 }))
    expect(lc.isPaymentEnvelope).toBe(false)
    expect(lc.availableForPayment).toBe(0)
    expect(lc.paymentUnderfunded).toBe(0)
    expect(lc.status).toBe('overspent')
  })

  it('overpaid payment shape → fields mapped, status forced to none (the blocker regression)', () => {
    const lc = mapRow(
      mkRow({
        key: 'env-cc',
        label: 'Meridian Bank Visa',
        available: -4000.0,
        is_payment_envelope: true,
        available_for_payment: -4000.0,
        underfunded: 7788.5,
      }),
    )
    expect(lc.isPaymentEnvelope).toBe(true)
    expect(lc.availableForPayment).toBe(-4000.0)
    expect(lc.paymentUnderfunded).toBe(7788.5)
    expect(lc.status).toBe('none') // NOT 'overspent', despite available < 0
  })
})

describe('target-based aggregations exclude payment envelopes (UI spec §6)', () => {
  // ...with ONE exception, added after verifying against YNAB: a CASH-overpaid card counts in
  // the Overspent badge/chip. The money-writing bulk auto-assign below still never touches it.

  const overspentOrdinary = mkCat({
    key: 'dining',
    label: 'Dining',
    envelopeId: 'dining',
    categoryIds: ['dining'],
    available: -30,
    status: 'overspent',
  })
  // A payment envelope as mapCat now produces it: status 'none', negative availableForPayment.
  const overpaidCard = mkPaymentCat({
    key: 'env-cc',
    label: 'Meridian Bank Visa',
    envelopeId: 'env-cc',
    available: -4000.0,
    availableForPayment: -4000.0,
    paymentUnderfunded: 7788.5,
    status: 'none',
  })
  const fundedCard = mkPaymentCat({
    key: 'env-cc2',
    label: 'Meridian Bank',
    envelopeId: 'env-cc2',
    available: 200,
    availableForPayment: 200,
    paymentUnderfunded: 0,
    status: 'none',
  })

  it('buildFilterCounts: the target chips exclude the cards; Overspent counts the overpaid one', () => {
    const counts = buildFilterCounts([overspentOrdinary, overpaidCard, fundedCard])
    // A cash-overpaid card is a real overspend and YNAB badges it as one (budget.filterCounts
    // test) — the FUNDED card is still nowhere.
    expect(counts.overspent).toBe(2)
    expect(counts.funded).toBe(0) // the funded card's positive available must NOT inflate "Money available"
    expect(counts.all).toBe(1) // both cards excluded from the TARGET-based row universe
    expect(counts.underfunded).toBe(0)
    expect(counts.overfunded).toBe(0)
  })

  it('buildStatusTotals sums the ordinary row AND the overpaid card, never the card DEBT figure', () => {
    const t = buildStatusTotals([overspentOrdinary, overpaidCard])
    expect(t.overspentCount).toBe(2)
    expect(t.overspentAmount).toBeCloseTo(4030.0, 2) // 30 + the 4000.00 the card was overpaid by
    // 7788.50 of card debt (paymentUnderfunded) is a DIFFERENT bucket and never lands here.
    expect(t.underfundedCount).toBe(0)
    expect(t.underfundedAmount).toBe(0)
  })

  it('buildClientAutoSteps "Top up overspent envelopes" never moves money into a card (the money-write bug)', () => {
    const cardOnly = buildClientAutoSteps([overpaidCard], 'Top up overspent envelopes')
    expect(cardOnly).toEqual([]) // no step → no assign() into the locked payment envelope
    const mixed = buildClientAutoSteps(
      [overspentOrdinary, overpaidCard],
      'Top up overspent envelopes',
    )
    expect(mixed).toHaveLength(1)
    expect(mixed[0]!.key).toBe('dining')
  })

  it('buildClientAutoSteps "Reset Assigned Amounts" also leaves a card with assigned money untouched', () => {
    const assignedCard = mkPaymentCat({
      key: 'env-cc3',
      label: 'Amex',
      envelopeId: 'env-cc3',
      assigned: 90,
      availableForPayment: 90,
      paymentUnderfunded: 0,
      status: 'none',
    })
    expect(buildClientAutoSteps([assignedCard], 'Reset Assigned Amounts')).toEqual([])
  })
})
