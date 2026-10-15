// @vitest-environment jsdom
// Regression guard for the Holdings "Group by" default (changed asset_class → none).
import { describe, it, expect, beforeEach } from 'vitest'
import { loadGroupBy } from './InvestmentsTab'

const KEY = 'inv-holdings-groupby'

describe('loadGroupBy — default grouping', () => {
  beforeEach(() => localStorage.clear())

  it("defaults to 'none' (No grouping) for a fresh user", () => {
    expect(loadGroupBy()).toBe('none')
  })

  it('respects a previously-saved valid preference', () => {
    localStorage.setItem(KEY, 'asset_class')
    expect(loadGroupBy()).toBe('asset_class')
  })

  it("falls back to 'none' for an invalid stored value", () => {
    localStorage.setItem(KEY, 'bogus')
    expect(loadGroupBy()).toBe('none')
  })
})
