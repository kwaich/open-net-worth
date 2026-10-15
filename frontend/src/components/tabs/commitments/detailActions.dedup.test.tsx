// A funding source and a real action can describe the SAME event. `derivedActions` synthesizes
// a pseudo-action per funding source, and its only dedup test was `a.due_date === f.available_date`
// — an EXACT date equality. Regression case: funding source "Dana Cole
// repayment (₫340M)" dated 2026-04-24 vs. action "Receive Dana Cole repayment (SGD 18k / ₫340M)"
// dated 2026-11-08, same S$18,000. Dates differ, so the list showed the same money twice.
// `action.funding_id` is the designed link between the two and was never consulted here.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { Actions } from './detailActions'
import { setBaseCurrency } from '../../../utils/format'
import type { Commitment, CommitmentAction, FundingSource } from '../../../types/commitments'
import type { DetailMutations } from './detailTypes'

setBaseCurrency('SGD')
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const m = {} as unknown as DetailMutations

const namFunding: FundingSource = {
  id: 'fs-nam',
  type: 'windfall',
  label: 'Dana Cole repayment (₫340M)',
  amount: 18000,
  currency: 'SGD',
  ref_position_id: null,
  sell_quantity: null,
  available_date: '2026-04-24',
  ref_account_id: null,
  repayment_commitment_id: null,
  note: '',
  disabled: false,
  cadence: null,
  status: 'planned',
  realized_txn_id: null,
}
const namAction = (over: Partial<CommitmentAction> = {}): CommitmentAction => ({
  id: 'a-nam',
  label: 'Receive Dana Cole repayment (SGD 18k / ₫340M)',
  category: 'Cash Transfer',
  due_date: '2026-11-08',
  start_by: null,
  amount: 18000,
  currency: 'SGD',
  status: 'pending',
  counterparty: '',
  note: '',
  funding_id: 'fs-nam',
  reminder_automation_id: null,
  ...over,
})

const base: Commitment = {
  id: 'c1',
  name: 'Parkview Condo',
  kind: 'purchase',
  currency: 'SGD',
  status: 'active',
  linked_position_id: null,
  linked_account_id: null,
  note: '',
  created_at: '2026-01-01T00:00:00Z',
  installments: [],
  funding_plan: [],
  actions: [],
}

const namRows = (container: HTMLElement) =>
  [...container.querySelectorAll('.c2-act-t')]
    .map((e) => e.textContent || '')
    .filter((t) => t.includes('Dana Cole repayment'))

describe('funding-plan derived actions are not duplicates of real ones', () => {
  it('suppresses the derived row when an action already links to that source via funding_id', () => {
    const c = { ...base, funding_plan: [namFunding], actions: [namAction()] }
    const { container } = render(<Actions commitment={c} fx={{ SGD: 1 }} m={m} />)
    expect(namRows(container)).toHaveLength(1)
  })

  it('suppresses the derived row for an UNLINKED action of the same amount within a month', () => {
    const c = {
      ...base,
      funding_plan: [namFunding],
      actions: [namAction({ funding_id: null, due_date: '2026-05-12' })],
    }
    const { container } = render(<Actions commitment={c} fx={{ SGD: 1 }} m={m} />)
    expect(namRows(container)).toHaveLength(1)
  })

  it('still shows the derived row when no action describes that source at all', () => {
    const c = { ...base, funding_plan: [namFunding], actions: [] }
    const { container } = render(<Actions commitment={c} fx={{ SGD: 1 }} m={m} />)
    expect(namRows(container)).toHaveLength(1)
  })
})
