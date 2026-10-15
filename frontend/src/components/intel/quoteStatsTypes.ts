export interface Stats {
  prev_close?: number
  open?: number
  day_low?: number
  day_high?: number
  week52_low?: number
  week52_high?: number
  market_cap?: number
  volume?: number
  pe_ratio?: number
  eps?: number
  dividend_yield?: number
  // Crypto shape (kind === 'crypto', sourced from CoinGecko /coins/{id} market_data —
  // day_low/day_high above carry the 24h range; supplies are coin units, currency-free)
  fdv?: number
  volume_24h?: number
  vol_to_mcap?: number
  ath?: number
  atl?: number
  circulating_supply?: number
  total_supply?: number
  max_supply?: number
  genesis_date?: string
}
