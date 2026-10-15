// Regression test — mobile UI/UX review, owner-caught screenshot: a funding row showed
// "Scheduled draw" (née "One-time draw") · "Recurring savings" · then the literal word
// "recurring" in the amount column, for a MANUALLY-DATED monthly set-aside entry that
// (unlike the live-synced Budget envelope) has its own real, concrete dollar figure —
// hydrateFundingPlan (fundingValue.ts) always computes one, and consecutive months can
// genuinely differ (e.g. before/after a loan repayment starts eating into free cash flow).
// The amount column's condition was `f.type === 'income'`, true for BOTH the one envelope-
// synced source AND every hand-entered income-type row, so it hid a real, varying number
// behind the same vague word for all of them — the one place on the row where two different
// months could otherwise be told apart. Fixed to `isEnvelopeSource(f)`.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { Funding } from './detailFunding'
import { setBaseCurrency } from '../../../utils/format'
import type { Commitment, FundingSource } from '../../../types/commitments'
import type { DetailMutations } from './detailTypes'

setBaseCurrency('SGD')

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => ({ matches: [] }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function source(overrides: Partial<FundingSource>): FundingSource {
  return {
    id: 'fs1',
    type: 'income',
    label: 'Set-aside Aug 2026',
    amount: 4000,
    currency: 'SGD',
    ref_position_id: null,
    sell_quantity: null,
    available_date: '2026-08-31',
    ref_account_id: null,
    repayment_commitment_id: null,
    note: '',
    disabled: false,
    cadence: null,
    status: 'planned',
    realized_txn_id: null,
    value_base: 4000,
    ...overrides,
  }
}

function commitmentWith(funding_plan: FundingSource[]): Commitment {
  return {
    id: 'c1',
    name: 'Test commitment',
    kind: 'purchase',
    currency: 'SGD',
    status: 'active',
    linked_position_id: null,
    linked_account_id: null,
    note: '',
    created_at: '2026-01-01T00:00:00Z',
    installments: [
      {
        id: 'i1',
        label: '1st',
        description: '',
        due_date: '2026-09-01',
        amount: 84000,
        status: 'upcoming',
        paid_txn_id: null,
      },
    ],
    funding_plan,
  } as unknown as Commitment
}

const m = {
  onAskLucius: vi.fn(),
  onOpenCommitment: vi.fn(),
  onOpenTxn: vi.fn(),
  updateInstallment: vi.fn(),
  removeInstallment: vi.fn(),
  payInstallment: vi.fn(),
  linkTxn: vi.fn(),
  updateFunding: vi.fn(),
  removeFunding: vi.fn(),
  addFunding: vi.fn(),
  updateAction: vi.fn(),
  addAction: vi.fn(),
  setActionMissed: vi.fn(),
  rename: vi.fn(),
  updateNote: vi.fn(),
  setStatus: vi.fn(),
  deleteCommitment: vi.fn(),
} as unknown as DetailMutations

describe('Funding tab — a manually-dated income row shows its own real amount, not "recurring"', () => {
  it('shows the actual dollar figure for a hand-entered monthly set-aside (cadence: null)', () => {
    const c = commitmentWith([source({ id: 'aug', label: 'Set-aside Aug 2026', value_base: 4000 })])
    const { container } = render(
      <Funding commitment={c} fx={{ SGD: 1 }} envelope={null} positions={{}} m={m} />,
    )
    const amt = container.querySelector('.c2-fund-amt')
    expect(amt?.textContent).toContain('4,000')
    expect(amt?.textContent).not.toBe('recurring')
  })

  it('two hand-entered monthly rows with DIFFERENT amounts are distinguishable in the amount column', () => {
    const c = commitmentWith([
      source({
        id: 'aug',
        label: 'Set-aside Aug 2026',
        available_date: '2026-08-31',
        value_base: 4000,
      }),
      source({
        id: 'oct',
        label: 'Set-aside Oct 2026',
        available_date: '2026-10-31',
        value_base: 3000,
      }),
    ])
    const { container } = render(
      <Funding commitment={c} fx={{ SGD: 1 }} envelope={null} positions={{}} m={m} />,
    )
    const amounts = Array.from(container.querySelectorAll('.c2-fund-amt')).map(
      (el) => el.textContent,
    )
    expect(amounts.some((t) => t?.includes('4,000'))).toBe(true)
    expect(amounts.some((t) => t?.includes('3,000'))).toBe(true)
  })

  it('still shows "recurring" for the ONE live-synced Budget envelope source (cadence: monthly)', () => {
    const c = commitmentWith([source({ id: 'env', cadence: 'monthly', value_base: 4000 })])
    const { container } = render(
      <Funding commitment={c} fx={{ SGD: 1 }} envelope={null} positions={{}} m={m} />,
    )
    expect(container.querySelector('.c2-fund-amt')?.textContent).toBe('recurring')
  })

  // PENDING a `draw` funding-source type: FUNDING_LABELS has no `draw` key and the model has
  // no such type, so "Scheduled draw" cannot render yet. This is the spec for that rename,
  // written ahead of it (4d485ca3). Un-skip with the type.
  it.skip('tags a hand-entered income row "Scheduled draw", not the self-contradictory "One-time draw"', () => {
    const c = commitmentWith([source({ id: 'aug', value_base: 4000 })])
    const { container } = render(
      <Funding commitment={c} fx={{ SGD: 1 }} envelope={null} positions={{}} m={m} />,
    )
    const tag = container.querySelector('.c3-fund-tag.draw')
    expect(tag?.textContent).toBe('Scheduled draw')
    expect(container.textContent).not.toContain('One-time draw')
  })

  it('still tags the envelope-synced source "Budget target"', () => {
    const c = commitmentWith([source({ id: 'env', cadence: 'monthly', value_base: 4000 })])
    const { container } = render(
      <Funding commitment={c} fx={{ SGD: 1 }} envelope={null} positions={{}} m={m} />,
    )
    expect(container.querySelector('.c3-fund-tag.env')?.textContent).toBe('Budget target')
  })
})
