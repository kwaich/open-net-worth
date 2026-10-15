// @vitest-environment jsdom
//
// `grant` is a registered Lucius mention type (backend descriptor
// services/mentions/descriptors/equity_lots.py, order=21). Its only emitter used to be
// tabs/equity/GrantList.tsx — a component nothing rendered, deleted 2026-09-22 with the
// rest of the unreachable EquitySubTab tree. The LIVE screen (this tab) emitted plain
// <tr>s, so in practice a grant had not been draggable / "Ask Lucius about this"-able
// since the Investments design port; tests/test_mention_type_frontend_coverage.py caught
// it the moment the dead emitter went away.
//
// dragRightClick binds globally on [data-entity-id][draggable]; the attributes ARE the
// wiring, so this locks them in at the leaf component — the same shape as
// tabs/commitments/lucius.entityWiring.test.tsx.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'

const useEquityView = vi.fn()
vi.mock('./useEquityView', () => ({ useEquityView: () => useEquityView() }))

import { EquityTab } from './EquityTab'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const rawGrant = {
  id: 'grant_1',
  type: 'rsu',
  instrument_id: 'inst_1',
  symbol: 'KSTL',
  grant_date: '2026-03-15',
  total_units: 1955,
  vested_units: 978,
  unvested_units: 977,
  next_vest_date: '2026-12-15',
  currency: 'EUR',
  grant_price_per_unit: 20.36,
  tax_event: 'at_vest',
  status: 'active',
  release_required: false,
  pending_release_units: 0,
  locked_position_id: 'pos_1',
  pending_position_id: null,
  account_id: 'acct_1',
  tranches: [],
  note: 'Joining grant',
  created_at: '2026-03-15T00:00:00Z',
  updated_at: '2026-03-15T00:00:00Z',
}

function renderTab() {
  useEquityView.mockReturnValue({
    loading: false,
    error: false,
    summaryCards: [],
    rawGrants: [rawGrant],
    upcoming: [],
    vestHistory: [],
    refetch: () => {},
    grants: [
      {
        id: 'grant_1',
        symbol: 'KSTL',
        grantDate: '2026-03-15',
        grantPrice: 20.36,
        currency: 'EUR',
        total: 1955,
        vested: 978,
        unvested: 977,
        nextVest: '2026-12-15',
        hasOverdue: false,
        taxEvent: 'at_vest',
        status: 'active',
        badgeLabel: 'Active',
        badgeTone: 'neutral',
        pct: 50,
        sinceGrantLine: null,
        tranches: [],
      },
    ],
  })
  return render(<EquityTab />)
}

describe('EquityTab grant row Lucius wiring', () => {
  it('emits the entity id, label and draggable so drag + Ask Lucius work', () => {
    const { container } = renderTab()

    const row = container.querySelector<HTMLElement>('[data-entity-id="grant:grant_1"]')
    expect(row).toBeTruthy()
    expect(row!.getAttribute('data-entity-label')).toBe('KSTL')
    expect(row!.getAttribute('draggable')).toBe('true')
  })

  it('emits the descriptor-allowlisted context fields for the screen snapshot', () => {
    const { container } = renderTab()

    const row = container.querySelector<HTMLElement>('[data-entity-id="grant:grant_1"]')
    expect(row!.getAttribute('data-ctx-type')).toBe('grant')
    const fields = row!.getAttribute('data-ctx-fields') || ''
    // The raw Grant is what feeds toFields — the view model carries neither tax_event
    // spelled this way nor the note, and a grant's own descriptor allowlists both.
    expect(fields).toContain('KSTL')
    expect(fields).toContain('at_vest')
  })
})
