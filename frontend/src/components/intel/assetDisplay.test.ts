import { describe, it, expect } from 'vitest'
import { displayTicker } from './assetDisplay'

// The security-suffix strip moved server-side (backend/core/instrument_display.py,
// covered by tests/test_instrument_display.py). Only the crypto-symbol logic remains here.

describe('displayTicker', () => {
  it('shows the symbol for crypto, not the CoinGecko slug', () => {
    expect(displayTicker({ asset_class: 'crypto', symbol: 'SHIB', ticker: 'shiba-inu' })).toBe(
      'SHIB',
    )
    expect(displayTicker({ asset_class: 'crypto', symbol: 'PEPE', ticker: 'pepe' })).toBe('PEPE')
  })

  it('keeps the exchange-qualified ticker for non-crypto', () => {
    expect(displayTicker({ asset_class: 'equity', symbol: 'ONWAI', ticker: 'ONWAI.DE' })).toBe(
      'ONWAI.DE',
    )
  })

  it('falls back to the ticker when a crypto symbol is missing', () => {
    expect(displayTicker({ asset_class: 'crypto', symbol: '', ticker: 'shiba-inu' })).toBe(
      'shiba-inu',
    )
  })

  it('handles mixed-case asset_class', () => {
    expect(displayTicker({ asset_class: 'Crypto', symbol: 'BTC', ticker: 'bitcoin' })).toBe('BTC')
  })

  it('returns empty string when nothing is available', () => {
    expect(displayTicker({ asset_class: 'crypto', symbol: '', ticker: null })).toBe('')
  })
})
