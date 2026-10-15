// The cushion chart's rendered height was `container width ÷ 5` and nothing capped it: a
// viewBox of `0 0 760 152` on an SVG at `width: 100%`. Measured on the owner's own screen —
// a ~1380px pane rendered the chart ~276px tall (a vast, near-empty plot holding six points),
// while a ~740px pane rendered ~148px (bars reduced to ~15px nubs). The chart was only ever
// the size it was spaced for at exactly 760px.
//
// A CSS height is NOT the fix — `preserveAspectRatio="none"` is set, so pinning height
// stretches Y alone: dots become ellipses and the bar band floats off the plot (see the
// comments in chart.tsx, and useNarrow.ts, for the two times that was tried).
//
// The fix is to make the viewBox map 1:1 to CSS pixels — WIDTH tracks the measured container,
// HEIGHT stays constant — so the chart is the same height at every pane width and extra width
// only gives the months more horizontal room.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { Chart } from './chart'
import { setBaseCurrency } from '../../../utils/format'
import type { CoverageTimelineMonth } from '../../../types/commitments'

setBaseCurrency('SGD')

const months: CoverageTimelineMonth[] = ['2026-08', '2026-09', '2026-10', '2026-11'].map(
  (ym, i) => ({
    year_month: ym,
    balance: 40000 - i * 5000,
    total_in: 21275,
    total_out: 20127,
    is_tight: false,
  }),
)

function widthIs(px: number) {
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get: () => px,
  })
}

beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      private cb: () => void
      constructor(cb: () => void) {
        this.cb = cb
      }
      observe() {
        this.cb()
      }
      unobserve() {}
      disconnect() {}
    },
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  // @ts-expect-error - restore jsdom's own zero-width default
  delete HTMLElement.prototype.clientWidth
})

const viewBox = (c: HTMLElement) => c.querySelector('.c2-chart-svg')!.getAttribute('viewBox')

describe('cushion chart geometry', () => {
  it('is the SAME height in a wide pane as in a narrow one', () => {
    widthIs(1380)
    const wide = render(<Chart timeline={months} floor={5000} gapIdx={-1} />)
    const wideBox = viewBox(wide.container)
    cleanup()

    widthIs(740)
    const narrowish = render(<Chart timeline={months} floor={5000} gapIdx={-1} />)

    expect(wideBox!.split(' ')[3]).toBe(viewBox(narrowish.container)!.split(' ')[3])
  })

  it('maps the viewBox 1:1 to CSS pixels, so height never scales with width', () => {
    widthIs(1380)
    const { container } = render(<Chart timeline={months} floor={5000} gapIdx={-1} />)
    expect(viewBox(container)).toBe('0 0 1380 152')
  })

  it('falls back to the designed 760x152 box before a measurement lands', () => {
    widthIs(0)
    const { container } = render(<Chart timeline={months} floor={5000} gapIdx={-1} />)
    expect(viewBox(container)).toBe('0 0 760 152')
  })
})
