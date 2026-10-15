// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

const refetch = vi.fn()
const useEquityView = vi.fn()

vi.mock('./useEquityView', () => ({ useEquityView: () => useEquityView() }))
vi.mock('../equity/MarkReleasedModal', () => ({
  MarkReleasedModal: ({
    item,
    onDone,
  }: {
    item: { vest_event_id: string }
    onDone: () => void
  }) => (
    <div>
      Release modal for {item.vest_event_id}
      <button onClick={onDone}>Complete release</button>
    </div>
  ),
}))

import { fDate } from '../../../utils/format'
import { EquityTab } from './EquityTab'

function historyItem(overrides: Record<string, unknown> = {}) {
  return {
    vestEventId: 'vest_pending',
    grantId: 'grant_1',
    trancheId: 'tranche_1',
    symbol: 'ONWAI',
    date: '2026-06-15',
    units: 500,
    releaseStatus: 'awaiting_release',
    releasedOn: null,
    expectedReleaseDate: '2026-06-20',
    overdue: true,
    fmvPerUnit: 20.0,
    fmvCurrency: 'EUR',
    releaseItem: {
      vest_event_id: 'vest_pending',
      grant_id: 'grant_1',
      tranche_id: 'tranche_1',
      symbol: 'ONWAI',
      instrument_id: 'inst_1',
      vest_date: '2026-06-15',
      units: 500,
      fmv_per_unit: 20.0,
      fmv_currency: 'EUR',
      expected_release_date: '2026-06-20',
      overdue: true,
      indicative_value_base: 12_345,
      base_currency: 'SGD',
    },
    ...overrides,
  }
}

describe('EquityTab vest history', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('keeps recorded vests visible with released versus awaiting-release status', () => {
    useEquityView.mockReturnValue({
      loading: false,
      error: false,
      summaryCards: [],
      grants: [],
      rawGrants: [],
      upcoming: [],
      vestHistory: [
        historyItem(),
        historyItem({
          vestEventId: 'vest_released',
          date: '2026-03-15',
          releaseStatus: 'released',
          releasedOn: '2026-03-18',
          overdue: false,
          releaseItem: null,
        }),
      ],
      refetch,
    })

    render(<EquityTab />)

    expect(screen.getByText('Vest history')).toBeTruthy()
    expect(screen.getByText('Awaiting release')).toBeTruthy()
    expect(screen.getByText('Released')).toBeTruthy()
    expect(screen.getByTitle(`Released ${fDate('2026-03-18', 'short', 'always')}`)).toBeTruthy()
    expect(screen.getAllByText('ONWAI')).toHaveLength(2)
  })

  it('shows the price at vest, formatted as currency, and falls back to an em dash when it is unknown', () => {
    useEquityView.mockReturnValue({
      loading: false,
      error: false,
      summaryCards: [],
      grants: [],
      rawGrants: [],
      upcoming: [],
      vestHistory: [
        historyItem({ vestEventId: 'vest_priced', fmvPerUnit: 20.0, fmvCurrency: 'EUR' }),
        historyItem({
          vestEventId: 'vest_legacy',
          date: '2026-01-15',
          fmvPerUnit: null,
          fmvCurrency: null,
          releaseStatus: 'not_released',
          releaseItem: null,
        }),
      ],
      refetch,
    })

    const { container } = render(<EquityTab />)

    expect(screen.getByText('Price at vest')).toBeTruthy()
    const priceCells = Array.from(container.querySelectorAll('.inv-event-price')).map(
      (el) => el.textContent,
    )
    expect(priceCells).toEqual(['€20.00', '—'])
  })

  it('opens the existing release flow from an awaiting history row', () => {
    useEquityView.mockReturnValue({
      loading: false,
      error: false,
      summaryCards: [],
      grants: [],
      rawGrants: [],
      upcoming: [],
      vestHistory: [historyItem()],
      refetch,
    })

    render(<EquityTab />)
    fireEvent.click(screen.getByRole('button', { name: 'Mark released' }))

    expect(screen.getByText('Release modal for vest_pending')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Complete release' }))
    expect(refetch).toHaveBeenCalledOnce()
  })

  it('shows a legacy vest as not released and keeps its action in the status column', () => {
    useEquityView.mockReturnValue({
      loading: false,
      error: false,
      summaryCards: [],
      grants: [],
      rawGrants: [],
      upcoming: [],
      vestHistory: [
        historyItem({
          releaseStatus: 'not_released',
          expectedReleaseDate: null,
          overdue: false,
        }),
      ],
      refetch,
    })

    render(<EquityTab />)

    expect(screen.getByText('Not released')).toBeTruthy()
    expect(screen.getByTitle('Release not recorded')).toBeTruthy()
    const action = screen.getByRole('button', { name: 'Mark released' })
    expect(action.closest('.inv-event-status')).not.toBeNull()
  })

  it('shows eight recorded vests before offering the complete history', () => {
    const vestHistory = Array.from({ length: 9 }, (_, index) =>
      historyItem({
        vestEventId: `vest_${index}`,
        date: `2026-06-${String(20 - index).padStart(2, '0')}`,
      }),
    )
    useEquityView.mockReturnValue({
      loading: false,
      error: false,
      summaryCards: [],
      grants: [],
      rawGrants: [],
      upcoming: [],
      vestHistory,
      refetch,
    })

    render(<EquityTab />)
    expect(screen.getAllByRole('button', { name: 'Mark released' })).toHaveLength(8)
    fireEvent.click(screen.getByRole('button', { name: 'Show all 9 vests' }))

    expect(screen.getAllByRole('button', { name: 'Mark released' })).toHaveLength(9)
    expect(screen.getByRole('button', { name: 'Show top 8 only' })).toBeTruthy()
  })

  it('shows one action when there are no grants or vests', () => {
    useEquityView.mockReturnValue({
      loading: false,
      error: false,
      summaryCards: [],
      grants: [],
      rawGrants: [],
      upcoming: [],
      vestHistory: [],
      refetch,
    })

    render(<EquityTab />)

    expect(screen.getByText('No equity grants yet')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /Add grant/ })).toHaveLength(1)
    expect(screen.queryByText('No vest history yet')).toBeNull()
  })
})
