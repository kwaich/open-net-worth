// Regression test for a real placement bug: the popover's height estimate grew unbounded
// with the source count (44 + n*30), so a segment with a long source list (e.g. 18 "Recurring
// savings" entries) computed a huge estimated height and got pushed almost to the top of the
// page, nowhere near the bar it was anchored to (owner: "it showing out of no where"). Fix:
// cap the estimate at the CSS .c2-fund-pop-rows max-height (240px, since the list scrolls
// internally past that) — the popover's real rendered height never exceeds it.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { FundingSourcesPopover } from './FundingSourcesPopover'
import type { CoverageBarSource } from './detailFunding'

afterEach(cleanup)

function rect(partial: Partial<DOMRect>): DOMRect {
  return {
    x: 0,
    y: 0,
    width: 100,
    height: 26,
    top: 0,
    left: 100,
    right: 200,
    bottom: 26,
    toJSON: () => ({}),
    ...partial,
  } as DOMRect
}

describe('FundingSourcesPopover — placement', () => {
  it('stays anchored near a low-on-page segment even with many sources (does not fly to the top)', () => {
    vi.stubGlobal('innerHeight', 800)
    vi.stubGlobal('innerWidth', 1200)
    const sources: CoverageBarSource[] = Array.from({ length: 18 }, (_, i) => ({
      id: `s${i}`,
      label: `Set-aside ${i}`,
      base: 1000,
    }))
    const anchor = rect({ top: 636, bottom: 662, left: 100, width: 100 })

    const { container } = render(
      <FundingSourcesPopover
        anchorRect={anchor}
        type="income"
        typeLabel="Recurring savings"
        typeTotal={18000}
        sources={sources}
        fundingPlan={[]}
        positions={{}}
        onMouseEnter={() => {}}
        onMouseLeave={() => {}}
      />,
    )
    const pop = container.querySelector('.c2-fund-pop') as HTMLDivElement
    const top = parseFloat(pop.style.top)
    // The estimate is capped at the CSS rows max-height (240) + head/padding (~58) — so even
    // with 18 sources, the popover lands within ~300px of the anchor, not near the page top.
    expect(anchor.top - top).toBeLessThan(320)
    vi.unstubAllGlobals()
  })

  it('places below the anchor when there is room', () => {
    vi.stubGlobal('innerHeight', 1200)
    vi.stubGlobal('innerWidth', 1200)
    const sources: CoverageBarSource[] = [{ id: 's1', label: 'One source', base: 1000 }]
    const anchor = rect({ top: 300, bottom: 326, left: 100, width: 100 })

    const { container } = render(
      <FundingSourcesPopover
        anchorRect={anchor}
        type="windfall"
        typeLabel="Windfall"
        typeTotal={1000}
        sources={sources}
        fundingPlan={[]}
        positions={{}}
        onMouseEnter={() => {}}
        onMouseLeave={() => {}}
      />,
    )
    const pop = container.querySelector('.c2-fund-pop') as HTMLDivElement
    const top = parseFloat(pop.style.top)
    expect(top).toBeGreaterThanOrEqual(anchor.bottom)
    vi.unstubAllGlobals()
  })
})
