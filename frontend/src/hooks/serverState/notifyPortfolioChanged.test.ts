import { beforeEach, describe, expect, it, vi } from 'vitest'

const emitDataChanged = vi.fn()
const invalidateQueryCache = vi.fn()

vi.mock('../../lucius/bus', () => ({
  emitDataChanged: (...args: unknown[]) => emitDataChanged(...args),
}))

vi.mock('../../state/queryCache', () => ({
  invalidateQueryCache: (...args: unknown[]) => invalidateQueryCache(...args),
}))

import { notifyPortfolioChanged } from './notifyPortfolioChanged'
import {
  NET_WORTH_KEY,
  NETWORTH_STATS_KEY,
  PORTFOLIO_ANALYTICS_KEY,
  PORTFOLIO_KEY,
  PORTFOLIO_LAST_KNOWN_KEY,
} from './keys'

beforeEach(() => {
  emitDataChanged.mockReset()
  invalidateQueryCache.mockReset()
})

describe('notifyPortfolioChanged', () => {
  it('invalidates portfolio-derived cache keys and emits portfolio+ledger', () => {
    notifyPortfolioChanged()

    expect(invalidateQueryCache.mock.calls.map((c) => c[0])).toEqual([
      PORTFOLIO_ANALYTICS_KEY,
      PORTFOLIO_KEY,
      PORTFOLIO_LAST_KNOWN_KEY,
      NET_WORTH_KEY,
      NETWORTH_STATS_KEY,
    ])
    expect(emitDataChanged).toHaveBeenCalledWith(['portfolio', 'ledger'])
  })
})
