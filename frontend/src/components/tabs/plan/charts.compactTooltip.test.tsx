// @vitest-environment jsdom

import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { setBaseCurrency, setDisplayFormats } from '../../../utils/format'
import type { BandRow } from './planData'
import { FanChart } from './charts'

const band = (age: number, p10: number, p50: number, p90: number): BandRow => ({
  year: 2030 + age,
  age,
  p10,
  p25: p10,
  p50,
  p75: p90,
  p90,
})

afterEach(() => {
  cleanup()
  setBaseCurrency('SGD')
  setDisplayFormats({})
})

describe('FanChart compact tooltip', () => {
  it('keeps exact base-currency values in titles for all three compact rows', () => {
    setBaseCurrency('VND')
    setDisplayFormats({ number_format: 'comma' })
    const bands = [
      band(40, 500_000_000, 1_000_000_000, 1_500_000_000),
      band(41, 1_000_000_000, 2_008_437_944, 3_000_000_000),
    ]
    const overlay = [band(40, 0, 1_250_000_000, 0), band(41, 0, 2_500_000_000, 0)]
    const { container } = render(<FanChart bands={bands} overlay={overlay} />)

    fireEvent.mouseMove(container.querySelector('.pl-chart-wrap')!, { clientX: 100 })

    expect(container.querySelector('.pl-chart-tip')?.textContent).toContain('₫2.01B')
    expect(
      Array.from(container.querySelectorAll('.pl-chart-tip b')).map((el) =>
        el.getAttribute('title'),
      ),
    ).toEqual(['₫2,008,437,944', '₫1,000,000,000–₫3,000,000,000', '₫2,500,000,000'])
  })
})
