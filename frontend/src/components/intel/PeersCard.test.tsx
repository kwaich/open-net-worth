// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen } from '@testing-library/react'
import { PeersCard } from './PeersCard'
import type { PeerItem } from '../../types/assetDetail'

// AssetLink (rendered per row) calls useAssetNav(), which throws outside its provider and
// the provider needs a router. Stub it — these tests only assert price/currency rendering.
vi.mock('../../state/assetNav', () => ({
  useAssetNav: () => ({ openAsset: vi.fn() }),
}))

afterEach(() => cleanup())

function makePeer(overrides: Partial<PeerItem> = {}): PeerItem {
  return {
    name: 'Prosus',
    ticker: 'PRX.AS',
    exchange: 'Amsterdam',
    price: 38.24,
    currency: 'EUR',
    change_pct: 0.07,
    ...overrides,
  }
}

describe('PeersCard — currency symbol on the price', () => {
  it('renders the price with its currency symbol (not the ISO code)', () => {
    const { container } = render(<PeersCard peers={[makePeer()]} />)
    const val = container.querySelector('.asd-peer-price-val')!
    expect(val.textContent).toContain('€')
    expect(val.textContent).toContain('38.24')
    expect(val.textContent).not.toContain('EUR') // symbol, not spelled-out code
  })

  it('uses each cross-listed peer its own symbol', () => {
    const { container } = render(
      <PeersCard
        peers={[
          makePeer(),
          makePeer({
            name: 'Uber',
            ticker: 'UBER',
            exchange: 'NYSE',
            price: 71.64,
            currency: 'USD',
          }),
        ]}
      />,
    )
    const vals = [...container.querySelectorAll('.asd-peer-price-val')].map((e) => e.textContent)
    expect(vals[0]).toContain('€')
    expect(vals[1]).toContain('$')
    expect(vals[1]).not.toContain('USD')
  })

  it('degrades to a bare price (no symbol) when currency is absent', () => {
    const { container } = render(<PeersCard peers={[makePeer({ currency: null })]} />)
    const val = container.querySelector('.asd-peer-price-val')!
    expect(val.textContent).toContain('38.24')
    expect(val.textContent).not.toMatch(/[€$]/) // no currency marker we can't verify
  })
})

// R3 fix (owner-reproduced live bug, 2026-08-31, KODK): an empty peers list with no
// `generating` prop used to render nothing at all — no skeleton, no indication a real
// search was still running.
describe('PeersCard — generating state', () => {
  it('renders nothing when peers is empty and not generating (unchanged default)', () => {
    const { container } = render(<PeersCard peers={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('shows a generating skeleton when peers is empty and a real pass is running', () => {
    render(<PeersCard peers={[]} generating />)
    expect(screen.getByRole('status', { name: 'Peers are generating' })).toBeTruthy()
  })

  it('shows real peers, not the generating skeleton, once they land even if generating is still true', () => {
    render(<PeersCard peers={[makePeer()]} generating />)
    expect(screen.queryByRole('status', { name: 'Peers are generating' })).toBeNull()
    expect(screen.getByText('Prosus')).toBeTruthy()
  })
})
