// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('./usePerformanceView', () => ({
  usePerformanceView: () => ({
    loading: false,
    error: null,
    data: { data_quality: { market_since: '2021-10-11' } },
    sufficient: false,
    returnCards: [],
    benchCards: [],
    benchmarks: [],
    risk: null,
  }),
}))

import { PerformanceTab } from './PerformanceTab'
import { takeSendIntent } from '../../../state/luciusSendIntent'

afterEach(cleanup)

it('with nothing held, pitches one return across every broker instead of a price-history note', () => {
  const onAddHolding = vi.fn()
  const onAskLucius = vi.fn()
  render(<PerformanceTab noHoldings onAddHolding={onAddHolding} onAskLucius={onAskLucius} />)
  expect(screen.getByText('Your whole portfolio vs the market')).toBeTruthy()
  expect(screen.queryByText(/Not enough price history/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Add holding' }))
  expect(onAddHolding).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole('button', { name: 'Add with Lucius' }))
  expect(onAskLucius).toHaveBeenCalledWith(expect.stringContaining('every broker'))
  expect(takeSendIntent()).toBe(true)
})

it('with holdings, keeps explaining a short price history', () => {
  render(<PerformanceTab />)
  expect(screen.getByText(/Not enough price history before 2021-10-11/)).toBeTruthy()
})
