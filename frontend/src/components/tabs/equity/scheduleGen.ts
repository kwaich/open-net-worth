// Pure client-side helpers for equity grant schedule generation and grant-letter parsing.
// No I/O, no side-effects — fully unit-testable.

export type Frequency = 'monthly' | 'quarterly' | 'annual'

export interface GeneratedTranche {
  vest_date: string // YYYY-MM-DD
  units: number
}

/**
 * Generate an evenly-distributed vesting schedule.
 *
 * Integer unit counts; any fractional remainder lands on the final tranche.
 * Returns [] for invalid inputs so callers can fall back to manual entry.
 */
export function eqGenSchedule(
  totalUnits: number,
  frequency: Frequency,
  years: number,
  firstVest: string, // YYYY-MM-DD
): GeneratedTranche[] {
  if (totalUnits <= 0 || years <= 0 || !/^\d{4}-\d{2}-\d{2}$/.test(firstVest)) return []

  const monthStep: Record<Frequency, number> = {
    monthly: 1,
    quarterly: 3,
    annual: 12,
  }
  const step = monthStep[frequency]
  const count = Math.round((years * 12) / step)
  if (count <= 0) return []

  const baseUnits = Math.floor(totalUnits / count)
  const remainder = totalUnits - baseUnits * count

  const parts = firstVest.split('-').map(Number)
  const y = parts[0]!
  const m = parts[1]!
  const d = parts[2]!

  return Array.from({ length: count }, (_, i) => {
    const totalMonths = m - 1 + i * step
    const year = y + Math.floor(totalMonths / 12)
    const month = (totalMonths % 12) + 1
    const day = Math.min(d, daysInMonth(year, month))
    const vest_date = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    const units = i === count - 1 ? baseUnits + remainder : baseUnits
    return { vest_date, units }
  })
}

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

// ── Grant-letter paste parser ────────────────────────────────────────────────

export interface ParsedGrant {
  symbol?: string
  totalUnits?: string
  freq?: Frequency
  years?: string
  firstVest?: string // YYYY-MM-DD
}

/**
 * Deterministic client-side parse of a free-text grant letter.
 *
 * Supports patterns like:
 *   "1,000 NVDA RSUs, quarterly over 4 years from 2026-09-15"
 *   "1000 TSLA RSU quarterly vesting over 3 years starting 2025-01-01"
 * Returns {} (empty object) when the text cannot be parsed — the caller
 * shows a "couldn't parse" hint and leaves the form fields empty.
 */
export function eqLuciusParse(text: string): ParsedGrant {
  if (!text || !text.trim()) return {}

  const result: ParsedGrant = {}

  // Total units — support comma-separated numbers
  const unitsMatch =
    text.match(/\b([\d,]+(?:\.\d+)?)\s+(?:units?|shares?|RSU|LTIP|ESOW)/i) ??
    text.match(/\b([\d,]+(?:\.\d+)?)\s+[A-Z]{2,6}\s+(?:RSU|LTIP|ESOW)/i)
  if (unitsMatch) {
    result.totalUnits = unitsMatch[1]!.replace(/,/g, '')
  }

  // Explicit tickers take precedence over words in the company name.
  const symbolMatch =
    text.match(/\b(?:ticker|symbol)\s*:?\s*([A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*)\b/i) ??
    text.match(/\b(?:[\d,]+(?:\.\d+)?)\s+([A-Z]{2,6})\s+(?:RSU|LTIP|ESOW|unit|share)/i) ??
    text.match(/\b([A-Z]{2,6}(?:[.-][A-Z0-9]+)?)\s+(?:RSU|LTIP|ESOW)/)
  if (symbolMatch) {
    result.symbol = symbolMatch[1]!.toUpperCase()
  }

  // Frequency
  if (/\bmonthly\b/i.test(text)) result.freq = 'monthly'
  else if (/\bquarterly\b/i.test(text)) result.freq = 'quarterly'
  else if (/\bannual(?:ly)?\b/i.test(text)) result.freq = 'annual'

  // Years
  const yearsMatch = text.match(/over\s+([\d]+)\s+year/i) ?? text.match(/([\d]+)[- ]year\b/i)
  if (yearsMatch) result.years = yearsMatch[1]

  // A grant or release date is not a vest date. Require vesting context.
  const dateMatch =
    text.match(/\bfirst\s+vest(?:ing)?(?:\s+date)?\s*(?:is|on|:)?\s*(\d{4}-\d{2}-\d{2})\b/i) ??
    text.match(
      /\bvesting\b(?:(?!\d{4}-).){0,160}?\b(?:on|from|starting)\s+(\d{4}-\d{2}-\d{2})\b/i,
    ) ??
    text.match(
      /\b(?:monthly|quarterly|annual(?:ly)?)\s+(?:vesting\s+)?over\s+\d+\s+years?\s+(?:from|starting)\s+(\d{4}-\d{2}-\d{2})\b/i,
    )
  if (dateMatch) result.firstVest = dateMatch[1]

  // Require at least symbol OR units to be non-empty (otherwise return nothing)
  if (!result.symbol && !result.totalUnits) return {}

  return result
}

/** Count how many top-level fields were populated (for the "filled N fields" note). */
export function countParsedFields(parsed: ParsedGrant): number {
  return Object.values(parsed).filter((v) => v !== undefined && v !== '').length
}
