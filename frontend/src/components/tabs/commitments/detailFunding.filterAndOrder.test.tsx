// Regression coverage for two owner requests on the Funding tab:
// (1) the synced-target block moved above the source list, not below it.
// (2) a type filter row lets the source list be narrowed instead of always showing every
//     source across every funding type at once.
// Also pins that a segment's inline $ label always renders now (owner: "let's not have that
// threshold at all") — no width-based cutoff hiding a narrow segment's amount.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { Funding } from './detailFunding'
import { setBaseCurrency } from '../../../utils/format'
import type { Commitment } from '../../../types/commitments'
import type { DetailMutations } from './detailTypes'
import type { EnvelopeAccrualState } from './fundingValue'

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

const commitment: Commitment = {
  id: 'c1',
  name: 'Test commitment',
  kind: 'purchase',
  currency: 'SGD',
  status: 'active',
  linked_position_id: null,
  linked_account_id: null,
  envelope_id: 'env_1',
  note: '',
  created_at: '2026-01-01T00:00:00Z',
  installments: [
    {
      id: 'i1',
      label: '1st',
      description: '',
      due_date: '2026-08-01',
      amount: 84000,
      status: 'upcoming',
      paid_txn_id: null,
    },
  ],
  funding_plan: [
    {
      id: 'w1',
      type: 'windfall',
      label: 'Bonus',
      amount: 30000,
      currency: 'SGD',
      ref_position_id: null,
      sell_quantity: null,
      available_date: null,
      ref_account_id: null,
      repayment_commitment_id: null,
      note: '',
      disabled: false,
      cadence: null,
      status: 'planned',
      realized_txn_id: null,
      value_base: 30000,
    },
    {
      id: 'loan-1',
      type: 'loan',
      label: 'Bank X FlexiLoan (24-mo)',
      amount: 50000,
      currency: 'SGD',
      ref_position_id: null,
      sell_quantity: null,
      available_date: null,
      ref_account_id: null,
      repayment_commitment_id: null,
      note: '',
      disabled: false,
      cadence: null,
      status: 'planned',
      realized_txn_id: null,
      value_base: 50000,
    },
  ],
}

const envelope: EnvelopeAccrualState = {
  envelope: 'Parkview Residences',
  accumulated: 21.15,
  per_period: 0,
  target_this_month: 4000,
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

describe('Funding tab — target placement, type filter, always-shown segment labels', () => {
  it('renders the synced-target block above the source list', () => {
    const { container } = render(
      <Funding commitment={commitment} fx={{ SGD: 1 }} envelope={envelope} positions={{}} m={m} />,
    )
    const section = container.querySelector('.c2-exp-sec')!
    const targetEl = section.querySelector('.c2-target')!
    const listEl = section.querySelector('.c2-fund-list')!
    expect(targetEl.compareDocumentPosition(listEl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('filters the source list by type when a filter chip is clicked', () => {
    const { container, getByRole } = render(
      <Funding commitment={commitment} fx={{ SGD: 1 }} envelope={envelope} positions={{}} m={m} />,
    )
    expect(container.querySelectorAll('.c2-fund-row')).toHaveLength(2)

    fireEvent.click(getByRole('button', { name: 'Loan (1)' }))
    const rows = container.querySelectorAll('.c2-fund-row')
    expect(rows).toHaveLength(1)
    expect(rows[0]!.textContent).toContain('Bank X FlexiLoan')

    fireEvent.click(getByRole('button', { name: 'All (2)' }))
    expect(container.querySelectorAll('.c2-fund-row')).toHaveLength(2)
  })

  it('does not show a type-filter row when every source shares one type', () => {
    const oneType: Commitment = { ...commitment, funding_plan: [commitment.funding_plan[0]!] }
    const { container } = render(
      <Funding commitment={oneType} fx={{ SGD: 1 }} envelope={envelope} positions={{}} m={m} />,
    )
    expect(container.querySelector('.c2-fund-typefilter')).toBeNull()
  })

  it("always shows a segment's inline $ label, even a narrow one (~14% of the bar)", () => {
    const { container } = render(
      <Funding commitment={commitment} fx={{ SGD: 1 }} envelope={envelope} positions={{}} m={m} />,
    )
    const loanSeg = container.querySelector('.ui-meter__segment.loan')!
    expect(loanSeg.textContent).toContain('$50K')
  })
})
