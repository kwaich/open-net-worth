// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DriverChips } from './DriverChips'

describe('DriverChips', () => {
  afterEach(cleanup)

  it('shows each instrument once when multiple accounts contribute the same driver', () => {
    render(
      <DriverChips
        gainers={[
          {
            instrument_id: 'eth',
            ticker: 'ETH',
            label: 'Ethereum',
            day_change_base: 60,
            day_change_pct: 2.91,
          },
          {
            instrument_id: 'eth',
            ticker: 'ETH',
            label: 'Ethereum',
            day_change_base: 40,
            day_change_pct: 2.91,
          },
        ]}
        losers={[
          {
            instrument_id: 'shib',
            ticker: 'SHIB',
            label: 'Shiba Inu',
            day_change_base: -8,
            day_change_pct: -0.97,
          },
          {
            instrument_id: 'shib',
            ticker: 'SHIB',
            label: 'Shiba Inu',
            day_change_base: -4,
            day_change_pct: -0.97,
          },
        ]}
        onOpenAsset={vi.fn()}
      />,
    )

    expect(screen.getAllByRole('button', { name: /ETH/ })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: /SHIB/ })).toHaveLength(1)
  })

  it('passes the ticker-backed driver through when its chip is opened', () => {
    const onOpenAsset = vi.fn()
    const pltr = {
      instrument_id: 'inst-pltr',
      ticker: 'PLTR',
      label: 'Palantir Technologies Inc.',
      day_change_base: 310,
      day_change_pct: 3.1,
    }

    render(<DriverChips gainers={[pltr]} losers={[]} onOpenAsset={onOpenAsset} />)

    fireEvent.click(screen.getByRole('button', { name: /PLTR/ }))
    expect(onOpenAsset).toHaveBeenCalledWith(pltr)
  })
})
