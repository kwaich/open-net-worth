// @vitest-environment jsdom
//
// KeyFactsCard renders crypto (real CoinGecko) facts vs equity (AI) facts.

import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { KeyFactsCard } from './KeyFactsCard'
import type { AssetInfo, AssetFacts, CryptoFacts } from '../../types/assetDetail'

afterEach(cleanup)

const ETH: AssetInfo = {
  name: 'Ethereum',
  symbol: 'ETH',
  ticker: 'ethereum', // CoinGecko slug
  exchange: null,
  mic: null,
  country: null,
  asset_class: 'crypto',
  currency: 'USD',
  description: null,
}

const EMPTY_FACTS: AssetFacts = {
  ceo: null,
  employees: null,
  ipo_date: null,
  industry: null,
  sector: null,
  exchange: null,
  country: null,
  unverified: true,
}

const ETH_CRYPTO: CryptoFacts = {
  category: 'Smart Contract Platform',
  network: null,
  market_cap: 451_000_000_000,
  market_cap_rank: 2,
  circulating_supply: 120_200_000,
  max_supply: null,
  ath: 4878.26,
  ath_date: '2021-11-10T14:24:19Z',
  genesis_date: '2015-07-30',
  homepage: 'https://www.ethereum.org/',
}

describe('KeyFactsCard — crypto', () => {
  it('renders real CoinGecko rows and the CoinGecko attribution', () => {
    render(<KeyFactsCard asset={ETH} facts={EMPTY_FACTS} cryptoFacts={ETH_CRYPTO} />)

    expect(screen.getByText('ETH')).toBeTruthy() // symbol, not the slug
    expect(screen.getByText('Smart Contract Platform')).toBeTruthy()
    expect(screen.getByText('$451B')).toBeTruthy()
    expect(screen.getByText('#2')).toBeTruthy()
    expect(screen.getByText('120.2M ETH')).toBeTruthy()
    expect(screen.getByText('$4,878.26 (Nov 2021)')).toBeTruthy()
    expect(screen.getByText('Data via CoinGecko')).toBeTruthy()

    // Website is a real link to the homepage.
    const link = screen.getByText('ethereum.org') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('https://www.ethereum.org/')

    // Equity rows / the AI disclaimer must NOT appear for crypto.
    expect(screen.queryByText('CEO')).toBeNull()
    expect(screen.queryByText('Employees')).toBeNull()
    expect(screen.queryByText(/AI-generated/)).toBeNull()
    // Uncapped supply → the row is hidden, not "—".
    expect(screen.queryByText('Max supply')).toBeNull()
  })
})

describe('KeyFactsCard — equity (unchanged)', () => {
  it('still renders the AI facts + disclaimer when there are no crypto facts', () => {
    const stock: AssetInfo = {
      ...ETH,
      name: 'ONWAI Inc',
      symbol: 'ONWAI',
      ticker: 'ONWAI.DE',
      asset_class: 'equity',
    }
    const facts: AssetFacts = { ...EMPTY_FACTS, sector: 'Consumer', industry: 'Retail' }
    render(<KeyFactsCard asset={stock} facts={facts} cryptoFacts={null} />)

    expect(screen.getByText('ONWAI.DE')).toBeTruthy()
    expect(screen.getByText('Consumer')).toBeTruthy()
    expect(screen.getByText('Retail')).toBeTruthy()
    expect(screen.getByText(/AI-generated/)).toBeTruthy()
    expect(screen.queryByText('Data via CoinGecko')).toBeNull()
  })
})

// R3 fix (owner-reproduced live bug, 2026-08-31, KODK): a cold-open with only the
// always-available Symbol/Exchange baseline (nothing from AI-synthesized `facts` yet) and
// no `generating` prop gave zero indication a real search was still running.
describe('KeyFactsCard — generating state', () => {
  const stock: AssetInfo = {
    ...ETH,
    name: 'Kodak',
    symbol: 'KODK',
    ticker: 'KODK',
    exchange: 'NYSE',
    asset_class: 'equity',
    description: null,
  }

  it('shows a generating badge + skeleton when only the sparse baseline has landed', () => {
    render(<KeyFactsCard asset={stock} facts={EMPTY_FACTS} cryptoFacts={null} generating />)
    expect(screen.getByRole('status', { name: 'Key facts are generating' })).toBeTruthy()
  })

  it('does not show generating when real facts have already landed, even if generating is still true', () => {
    const facts: AssetFacts = { ...EMPTY_FACTS, sector: 'Technology', industry: 'Electronics' }
    render(<KeyFactsCard asset={stock} facts={facts} cryptoFacts={null} generating />)
    expect(screen.queryByRole('status', { name: 'Key facts are generating' })).toBeNull()
    expect(screen.getByText('Technology')).toBeTruthy()
  })

  it('never shows generating for crypto facts (real-time data, never LLM-synthesized)', () => {
    render(<KeyFactsCard asset={ETH} facts={EMPTY_FACTS} cryptoFacts={ETH_CRYPTO} generating />)
    expect(screen.queryByRole('status', { name: 'Key facts are generating' })).toBeNull()
  })

  it('does not show generating when generating is false, even with only the sparse baseline', () => {
    render(<KeyFactsCard asset={stock} facts={EMPTY_FACTS} cryptoFacts={null} />)
    expect(screen.queryByRole('status', { name: 'Key facts are generating' })).toBeNull()
  })
})
