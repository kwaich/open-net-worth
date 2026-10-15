// Regression coverage for the funding-bar segment hover popover — turning the old plain
// title= tooltip ("Windfall: S$59,000.00") into a rich popup listing the individual sources
// behind that aggregate (owner request: "can we show a list of them instead?").
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, cleanup, fireEvent, act } from '@testing-library/react'
import { Funding } from './detailFunding'
import { setBaseCurrency } from '../../../utils/format'
import type { Commitment } from '../../../types/commitments'
import type { DetailMutations } from './detailTypes'

setBaseCurrency('SGD')

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => ({ matches: [] }),
}))

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.useRealTimers()
})

const commitment: Commitment = {
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
      due_date: '2026-08-01',
      amount: 30000,
      status: 'upcoming',
      paid_txn_id: null,
    },
  ],
  funding_plan: [
    {
      id: 'w1',
      type: 'windfall',
      label: 'Dana Cole repayment',
      amount: 18000,
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
      value_base: 18000,
    },
    {
      id: 'w2',
      type: 'windfall',
      label: 'Tax refund',
      amount: 34000,
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
      value_base: 34000,
    },
  ],
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

describe('Funding bar — segment hover popover', () => {
  it('opens after the hover-intent delay and lists both windfall sources', () => {
    const { container } = render(
      <Funding commitment={commitment} fx={{ SGD: 1 }} envelope={null} positions={{}} m={m} />,
    )
    const seg = container.querySelector('.ui-meter__segment.windfall')!
    expect(container.querySelector('.c2-fund-pop')).toBeNull()

    fireEvent.mouseEnter(seg)
    // Not open yet — still inside the intent delay.
    expect(container.querySelector('.c2-fund-pop')).toBeNull()
    act(() => vi.advanceTimersByTime(150))
    expect(container.querySelector('.c2-fund-pop')).toBeTruthy()

    const rows = container.querySelectorAll('.c2-fund-pop-row')
    expect(rows).toHaveLength(2)
    expect(container.querySelector('.c2-fund-pop')?.textContent).toContain('Dana Cole repayment')
    expect(container.querySelector('.c2-fund-pop')?.textContent).toContain('Tax refund')
  })

  it('closes on mouse-leave after the delay, but stays open if the pointer moves into the popover', () => {
    const { container } = render(
      <Funding commitment={commitment} fx={{ SGD: 1 }} envelope={null} positions={{}} m={m} />,
    )
    const seg = container.querySelector('.ui-meter__segment.windfall')!
    fireEvent.mouseEnter(seg)
    act(() => vi.advanceTimersByTime(150))
    expect(container.querySelector('.c2-fund-pop')).toBeTruthy()

    fireEvent.mouseLeave(seg)
    const pop = container.querySelector('.c2-fund-pop')!
    fireEvent.mouseEnter(pop) // pointer moved into the popover before the close delay elapses
    act(() => vi.advanceTimersByTime(150))
    expect(container.querySelector('.c2-fund-pop')).toBeTruthy()

    fireEvent.mouseLeave(pop)
    act(() => vi.advanceTimersByTime(150))
    expect(container.querySelector('.c2-fund-pop')).toBeNull()
  })

  it('shows label + amount for a segment backed by a single source (e.g. one loan draw)', () => {
    const loanOnly: Commitment = {
      ...commitment,
      installments: [
        // Due AFTER the loan's own available_date — installmentFunding only lets a draw
        // fund an installment it has actually arrived by, chronologically.
        {
          id: 'i1',
          label: '5th',
          description: '',
          due_date: '2026-11-20',
          amount: 50000,
          status: 'upcoming',
          paid_txn_id: null,
        },
      ],
      funding_plan: [
        {
          id: 'loan-1',
          type: 'loan',
          label: 'Bank X FlexiLoan (24-mo)',
          amount: 50000,
          currency: 'SGD',
          ref_position_id: null,
          sell_quantity: null,
          available_date: '2026-09-15',
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
    const { container } = render(
      <Funding commitment={loanOnly} fx={{ SGD: 1 }} envelope={null} positions={{}} m={m} />,
    )
    const seg = container.querySelector('.ui-meter__segment.loan')!
    expect(seg).toBeTruthy()
    fireEvent.mouseEnter(seg)
    act(() => vi.advanceTimersByTime(150))
    const pop = container.querySelector('.c2-fund-pop')
    expect(pop).toBeTruthy()
    expect(container.querySelectorAll('.c2-fund-pop-row')).toHaveLength(1)
    expect(pop?.textContent).toContain('Bank X FlexiLoan (24-mo)')
    expect(pop?.textContent).toContain('S$50,000.00')
  })

  it('does not render a native title= on type segments (aria-label instead)', () => {
    const { container } = render(
      <Funding commitment={commitment} fx={{ SGD: 1 }} envelope={null} positions={{}} m={m} />,
    )
    const seg = container.querySelector('.ui-meter__segment.windfall')!
    expect(seg.getAttribute('title')).toBeNull()
    expect(seg.getAttribute('aria-label')).toContain('Windfall')
  })
})
