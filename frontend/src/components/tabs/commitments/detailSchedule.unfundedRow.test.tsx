// Regression: an installment that NO funding source reaches (alloc is empty because an
// earlier installment — often one sharing the same due_date, e.g. Handover + Maintenance —
// already consumed the whole pool) rendered with NO "Funded by" box at all, so its
// "Unfunded S$X" badge was invisible. The fully-unfunded case is the worst case; it is
// exactly the one that must be visible. Owner-reported against a real installment schedule.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { Schedule } from './detailSchedule'
import { setBaseCurrency } from '../../../utils/format'
import type { Commitment } from '../../../types/commitments'
import type { DetailMutations } from './detailTypes'

setBaseCurrency('SGD')

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
vi.mock('../../../hooks/serverState', () => ({ ACCOUNTS_KEY: 'accounts', fetchJson: vi.fn() }))
vi.mock('../../../state/queryCache', () => ({ fetchCachedQuery: vi.fn(() => Promise.resolve([])) }))

afterEach(cleanup)

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

// Handover + Maintenance share a due_date; the single draw only covers Handover, leaving
// Maintenance with an empty alloc and its full amount unfunded.
const commitment: Commitment = {
  id: 'c1',
  name: 'Parkview-like',
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
      label: 'Handover',
      description: '',
      due_date: '2028-09-30',
      amount: 100000,
      status: 'upcoming',
      paid_txn_id: null,
    },
    {
      id: 'i2',
      label: 'Maintenance',
      description: '',
      due_date: '2028-09-30',
      amount: 8000,
      status: 'upcoming',
      paid_txn_id: null,
    },
  ],
  funding_plan: [
    {
      id: 'f1',
      type: 'windfall',
      label: 'Big windfall',
      amount: 100000,
      currency: 'SGD',
      ref_position_id: null,
      sell_quantity: null,
      available_date: '2028-09-01',
      ref_account_id: null,
      repayment_commitment_id: null,
      note: '',
      disabled: false,
      cadence: null,
      status: 'planned',
      realized_txn_id: null,
      value_base: 100000,
    },
  ],
}

describe('Schedule — fully-unfunded installment', () => {
  it('still renders the Unfunded badge when no source reaches the installment', () => {
    const { container } = render(
      <Schedule commitment={commitment} fx={{ SGD: 1 }} envelope={null} m={m} />,
    )

    // Both installments render a "Funded by" box — the second one purely to carry its
    // Unfunded badge (its alloc is empty).
    const boxes = container.querySelectorAll('.c3-fundby')
    expect(boxes).toHaveLength(2)

    const shortChips = container.querySelectorAll('.c3-fchip.short')
    expect(shortChips).toHaveLength(1)
    expect(shortChips[0]?.textContent).toContain('Unfunded')
    // The whole 8,000 is unfunded — the windfall was fully consumed by Handover.
    expect(shortChips[0]?.textContent).toContain('8,000')
  })
})
