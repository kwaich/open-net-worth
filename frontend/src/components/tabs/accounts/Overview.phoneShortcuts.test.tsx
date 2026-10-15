// @vitest-environment jsdom
//
// Phone shortcuts (log a spend from an Apple Pay tap) lives in Settings, but the place people
// think about logging spends is Accounts — so its ⋯ menu links there too.
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Overview, type OverviewStats, type OverviewTotals } from './Overview'

afterEach(() => cleanup())

const totals = {
  cash: 0,
  fixed_deposit: 0,
  investment: 0,
  retirement: 0,
  receivable: 0,
  credit: 0,
  loan: 0,
  liabilities: 0,
} as unknown as OverviewTotals
const noop = () => {}

function renderOverview(onPhoneShortcuts?: () => void) {
  return render(
    <Overview
      totals={totals}
      groups={[]}
      cashBalance={{ cleared: 0, uncleared: 0, total: 0 } as never}
      stats={{ count: 0, review: 0 } as OverviewStats}
      baseCurrency="SGD"
      collapsed={false}
      onReopen={noop}
      onAddTxn={noop}
      onAdd={noop}
      onManagePayees={noop}
      onManageCategories={noop}
      onImport={noop}
      onExport={noop}
      onReview={noop}
      onStartReview={noop}
      onShowUncleared={noop}
      onPhoneShortcuts={onPhoneShortcuts}
    />,
  )
}

it('opens Phone shortcuts from the ⋯ menu', () => {
  const open = vi.fn()
  renderOverview(open)
  fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
  fireEvent.click(screen.getByRole('button', { name: 'Phone shortcuts' }))
  expect(open).toHaveBeenCalledOnce()
  expect(screen.queryByRole('button', { name: 'Phone shortcuts' })).toBeNull()
})

it('leaves the item out when there is nowhere to navigate', () => {
  renderOverview(undefined)
  fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
  expect(screen.queryByRole('button', { name: 'Phone shortcuts' })).toBeNull()
})
