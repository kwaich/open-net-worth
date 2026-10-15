// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { ChartTypeMenu, readStoredChartType } from './ChartTypeMenu'

const STORAGE_KEY = 'asset-chart-type'

beforeEach(() => {
  localStorage.clear()
})

afterEach(cleanup)

describe('readStoredChartType', () => {
  it('defaults to line when nothing is stored', () => {
    expect(readStoredChartType()).toBe('line')
  })

  it('reads back a validly stored chart type', () => {
    localStorage.setItem(STORAGE_KEY, 'candle')
    expect(readStoredChartType()).toBe('candle')
  })

  it('falls back to line on a corrupt/unknown stored value', () => {
    localStorage.setItem(STORAGE_KEY, 'not-a-real-type')
    expect(readStoredChartType()).toBe('line')
  })
})

describe('ChartTypeMenu', () => {
  it('shows the given value as the active trigger label and checked row', () => {
    render(<ChartTypeMenu value="mountain" onChange={() => {}} ohlcAvailable={true} />)
    expect(screen.getByRole('button', { name: /chart type/i }).textContent).toContain('Mountain')
    fireEvent.click(screen.getByRole('button', { name: /chart type/i }))
    expect(
      screen.getByRole('menuitemradio', { name: /mountain/i }).getAttribute('aria-checked'),
    ).toBe('true')
  })

  it('disables Candle/Bar rows with a reason when ohlcAvailable is false', () => {
    render(<ChartTypeMenu value="line" onChange={() => {}} ohlcAvailable={false} />)
    fireEvent.click(screen.getByRole('button', { name: /chart type/i }))
    const candleRow = screen.getByRole('menuitemradio', { name: /candle/i }) as HTMLButtonElement
    const barRow = screen.getByRole('menuitemradio', { name: /bar/i }) as HTMLButtonElement
    expect(candleRow.disabled).toBe(true)
    expect(barRow.disabled).toBe(true)
    expect(candleRow.getAttribute('title')).toBe('No OHLC data for this range')
    // Line/Baseline/Mountain never need OHLC — always enabled.
    const lineRow = screen.getByRole('menuitemradio', { name: /^line$/i }) as HTMLButtonElement
    expect(lineRow.disabled).toBe(false)
  })

  it('persists the picked type to localStorage and calls onChange with the picked value', () => {
    let picked: string | null = null
    render(
      <ChartTypeMenu
        value="line"
        onChange={(v) => {
          picked = v
        }}
        ohlcAvailable={true}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /chart type/i }))
    fireEvent.click(screen.getByRole('menuitemradio', { name: /candle/i }))
    expect(picked).toBe('candle')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('candle')
  })

  // Regression: AssetPriceChart.tsx must pass the DEGRADED (effective) chart type as
  // `value`, not the raw persisted preference — otherwise the dropdown shows e.g.
  // "Candle" checked/active while the chart actually rendered as Line underneath.
  it('reflects a degraded display value distinctly from the caller-owned stored preference', () => {
    // Simulates AssetPriceChart passing effectiveChartType='line' while the user's
    // real stored pick ('candle') is unaffected until OHLC becomes available again.
    render(<ChartTypeMenu value="line" onChange={() => {}} ohlcAvailable={false} />)
    expect(screen.getByRole('button', { name: /chart type/i }).textContent).toContain('Line')
    fireEvent.click(screen.getByRole('button', { name: /chart type/i }))
    expect(
      screen.getByRole('menuitemradio', { name: /^line$/i }).getAttribute('aria-checked'),
    ).toBe('true')
    expect(
      screen.getByRole('menuitemradio', { name: /candle/i }).getAttribute('aria-checked'),
    ).toBe('false')
  })
})
