// Pure form logic for the Record/Close option modals — no React, fully unit-testable.
// Presets speak the user's language (overlays); the payload stores the RFC primitives.

export type StrategyPreset =
  | 'covered_call'
  | 'csp'
  | 'long_call'
  | 'long_put'
  | 'protective_put'
  | 'custom'

export const STRATEGY_PRESETS: { value: StrategyPreset; label: string }[] = [
  { value: 'covered_call', label: 'Covered call' },
  { value: 'csp', label: 'Cash-secured put' },
  { value: 'long_call', label: 'Long call' },
  { value: 'long_put', label: 'Long put' },
  { value: 'protective_put', label: 'Protective put' },
  { value: 'custom', label: 'Custom' },
]

export interface PresetDerivation {
  option_type: 'call' | 'put'
  side: 'long' | 'short'
  strategy_tag: string
}

export function deriveFromPreset(preset: StrategyPreset): PresetDerivation | null {
  switch (preset) {
    case 'covered_call':
      return { option_type: 'call', side: 'short', strategy_tag: 'covered_call' }
    case 'csp':
      return { option_type: 'put', side: 'short', strategy_tag: 'csp' }
    case 'long_call':
      return { option_type: 'call', side: 'long', strategy_tag: 'long_call' }
    case 'long_put':
      return { option_type: 'put', side: 'long', strategy_tag: 'long_put' }
    case 'protective_put':
      return { option_type: 'put', side: 'long', strategy_tag: 'protective_put' }
    case 'custom':
      return null
  }
}

// OCC convention: premium is quoted per SHARE — 2.50 on 1 contract (×100) = 250 cash.
export function totalPremium(contracts: number, multiplier: number, pps: number): number {
  return Math.round(contracts * multiplier * pps * 100) / 100
}

export function cspCollateral(strike: number, contracts: number, multiplier: number): number {
  return Math.round(strike * contracts * multiplier * 100) / 100
}

export interface OpenOptionDraft {
  underlying_instrument_id: string
  account_id: string
  option_type: 'call' | 'put'
  side: 'long' | 'short'
  strike: string
  expiry: string
  contracts: string
  multiplier: string
  premium_per_share: string
  premium_currency: string
  open_date: string
  strategy_tag: string
  premium_account_id: string
  note: string
  allow_duplicate?: boolean
}

export function validateDraft(d: OpenOptionDraft): string | null {
  if (!d.underlying_instrument_id) return 'Pick the underlying instrument.'
  if (!d.account_id) return 'Pick the securities account.'
  const strike = Number(d.strike)
  if (!d.strike || !Number.isFinite(strike) || strike <= 0) return 'Strike must be > 0.'
  if (!d.expiry) return 'Pick the expiry date.'
  const contracts = Number(d.contracts)
  if (!d.contracts || !Number.isFinite(contracts) || contracts <= 0) return 'Contracts must be > 0.'
  const multiplier = Number(d.multiplier)
  if (!Number.isFinite(multiplier) || multiplier <= 0 || !Number.isInteger(multiplier))
    return 'Multiplier must be a positive whole number.'
  const pps = Number(d.premium_per_share || 0)
  if (!Number.isFinite(pps) || pps < 0) return 'Premium per share cannot be negative.'
  return null
}

export function draftToPayload(d: OpenOptionDraft): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    underlying_instrument_id: d.underlying_instrument_id,
    account_id: d.account_id,
    option_type: d.option_type,
    side: d.side,
    strike: Number(d.strike),
    expiry: d.expiry,
    contracts: Number(d.contracts),
    multiplier: Number(d.multiplier),
    premium_per_share: Number(d.premium_per_share || 0),
    premium_currency: d.premium_currency || 'USD',
  }
  if (d.open_date) payload.open_date = d.open_date
  if (d.strategy_tag) payload.strategy_tag = d.strategy_tag
  if (d.premium_account_id) payload.premium_account_id = d.premium_account_id
  if (d.note) payload.note = d.note
  if (d.allow_duplicate) payload.allow_duplicate = true
  return payload
}
