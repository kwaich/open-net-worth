// Regression guard for owner feedback ("they are still not the same") — the Funding tab's
// "How it's funded" bar and the Overview tab's condensed "Funding" digest must render a
// byte-identical Meter for the same commitment, since both now go through the same
// shared FundingCoverageBar component instead of two hand-maintained copies.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { Funding } from './detailFunding'
import { OverviewTab } from './detailOverview'
import { setBaseCurrency } from '../../../utils/format'
import type { Commitment } from '../../../types/commitments'
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

describe('Funding bar parity — Funding tab vs. Overview digest', () => {
  it('renders an identical Meter for the same commitment', () => {
    const funding = render(
      <Funding commitment={commitment} fx={{ SGD: 1 }} envelope={null} positions={{}} m={m} />,
    )
    const fundingBarHtml = funding.container.querySelector('.ui-meter')!.outerHTML
    cleanup()

    const overview = render(
      <OverviewTab
        commitment={commitment}
        fx={{ SGD: 1 }}
        envelope={null}
        positions={{}}
        goTo={() => {}}
        m={m}
      />,
    )
    const overviewBarHtml = overview.container.querySelector('.ui-meter')!.outerHTML

    expect(overviewBarHtml).toBe(fundingBarHtml)
  })
})
