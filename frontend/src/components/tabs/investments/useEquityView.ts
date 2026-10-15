import { useCallback, useEffect, useState } from 'react'
import { useDataVersion } from '../../../hooks/useDataVersion'
import { authFetch } from '../../../api/client'
import type {
  Grant,
  GrantsResponse,
  PendingReleaseItem,
  UpcomingVestItem,
  UpcomingVestsResponse,
  VestHistoryItem,
  VestHistoryResponse,
} from '../../../types/equity'
import type { ComputedRow } from '../../../types/portfolio'
import { fDate, fNum } from '../../../utils/format'
import { ViewCache } from './viewCache'

interface EquityData {
  grants: Grant[]
  upcoming: UpcomingVestItem[]
  vestHistory: VestHistoryItem[]
}

// Module-level cache — single entry, keyed 'equity'.
const _cache = new ViewCache<EquityData>('equity')
const CACHE_KEY = 'equity'

const today = (): string => new Date().toISOString().slice(0, 10)

/** Relative-date label matching the prototype's eqRel() function. */
function relDate(iso: string): string {
  const d = Math.round((Date.parse(iso) - Date.parse(today())) / 86400000)
  if (d === 0) return 'today'
  if (d > 0) return `in ${d} day${d > 1 ? 's' : ''}`
  const abs = Math.abs(d)
  return `${abs} day${abs > 1 ? 's' : ''} ago`
}

// ── Summary cards ─────────────────────────────────────────────────────────────

export interface EquitySummaryCard {
  label: string
  value: string
  caption?: string
}

function buildSummaryCards(grants: Grant[]): EquitySummaryCard[] {
  const active = grants.filter((g) => g.status !== 'cancelled' && g.status !== 'forfeited')
  const totalVested = active.reduce((s, g) => s + g.vested_units, 0)
  const totalUnvested = active.reduce((s, g) => s + g.unvested_units, 0)

  let nextDate: string | null = null
  let nextSymbol = ''
  let nextUnits = 0
  for (const g of active) {
    for (const t of g.tranches) {
      if (t.vested) continue
      if (nextDate == null || t.vest_date < nextDate) {
        nextDate = t.vest_date
        nextSymbol = g.symbol
        nextUnits = t.units
      }
    }
  }

  return [
    { label: 'Total grants', value: String(active.length) },
    { label: 'Vested units', value: fNum(totalVested) },
    { label: 'Unvested units', value: fNum(totalUnvested) },
    {
      label: 'Next vest',
      value: nextDate ? fDate(nextDate, 'short', 'always') : '—',
      caption: nextDate ? `${nextSymbol} • ${fNum(nextUnits)} units` : undefined,
    },
  ]
}

// ── Tranche view model ────────────────────────────────────────────────────────

export type TrancheCls = 'is-vested' | 'is-next' | 'is-upcoming'
export type TrancheBadgeTone = 'pos' | 'neg' | 'neutral'

export interface TrancheVM {
  id: string
  date: string
  units: number
  kind: string
  vested: boolean
  cls: TrancheCls
  badgeLabel: string
  badgeTone: TrancheBadgeTone
  sub: string
  overdue: boolean
}

function buildTranche(t: Grant['tranches'][number], isNext: boolean): TrancheVM {
  const now = today()
  const overdue = !t.vested && t.vest_date <= now
  const cls: TrancheCls = t.vested ? 'is-vested' : isNext ? 'is-next' : 'is-upcoming'

  let badgeLabel: string
  let badgeTone: TrancheBadgeTone
  if (t.vested) {
    badgeLabel = 'Vested'
    badgeTone = 'pos'
  } else if (overdue) {
    badgeLabel = 'Overdue'
    badgeTone = 'neg'
  } else if (t.kind === 'performance') {
    badgeLabel = 'Performance'
    badgeTone = 'neutral'
  } else {
    badgeLabel = 'Upcoming'
    badgeTone = 'neutral'
  }

  const sub = t.vested
    ? 'Vested'
    : overdue
      ? `Overdue • ${relDate(t.vest_date)}`
      : relDate(t.vest_date)

  return {
    id: t.id,
    date: t.vest_date,
    units: t.units,
    kind: t.kind,
    vested: t.vested,
    cls,
    badgeLabel,
    badgeTone,
    sub,
    overdue,
  }
}

// ── Current price lookup (for "since grant" on unvested units) ───────────────

/** Per-instrument current price + currency, derived from portfolio rows. The raw
 *  `price` field is denominated in `quote_currency`, not `currency` (see
 *  portfolio_rows._display_fields, the backend's own ref_pl precedent). */
export interface PriceEntry {
  price: number
  currency: string
}

export function buildPriceByInstrument(rows: ComputedRow[]): Record<string, PriceEntry> {
  const map: Record<string, PriceEntry> = {}
  for (const row of rows) {
    if (!row.instrument_id) continue
    const rawPrice = parseFloat(row.price)
    if (!isFinite(rawPrice) || rawPrice <= 0) continue
    const factor = row.quote_per_unit ?? 1
    const currency = row.quote_currency || row.currency
    if (!currency) continue
    map[row.instrument_id] = { price: rawPrice * factor, currency }
  }
  return map
}

// ── Grant view model ──────────────────────────────────────────────────────────

export type GrantBadgeTone = 'muted' | 'warn' | 'neutral'

export interface GrantVM {
  id: string
  symbol: string
  grantDate: string
  grantPrice: number | null
  currency: string
  total: number
  vested: number
  unvested: number
  nextVest: string | null
  hasOverdue: boolean
  taxEvent: string
  status: string
  badgeLabel: string
  badgeTone: GrantBadgeTone
  pct: number
  tranches: TrancheVM[]
  sinceGrantLine: string | null
}

/** Fixed-precision "CCY 20.36" formatting shared by the grant-price and current-price halves
 *  of `sinceGrantLine` — matches the pre-existing static string's precision exactly. */
function fmtGrantPrice(n: number, currency: string): string {
  return `${currency} ${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** "Granted at …" reference line. Static when there's nothing to compare against; becomes
 *  "Granted at … • now … • ±x.x% since grant (unvested units)" when a live price is available
 *  AND there are unvested units to compare it to AND currencies match — mirroring the backend's
 *  own ref_pl precedent (portfolio_rows._display_fields): once units vest their real cost basis
 *  is FMV-at-vest, not grant price, so a vested/total comparison against grant price would be
 *  misleading, not merely unavailable. Cross-currency is refused rather than FX-approximated. */
export function buildSinceGrantLine(
  g: Grant,
  priceByInstrument: Record<string, PriceEntry>,
): string | null {
  const grantPrice = g.grant_price_per_unit
  if (grantPrice == null) return null

  const formattedGrant = fmtGrantPrice(grantPrice, g.currency)
  const staticLine = `Granted at ${formattedGrant}`
  if (g.unvested_units <= 0 || grantPrice <= 0) return staticLine

  const entry = priceByInstrument[g.instrument_id]
  if (!entry || !g.currency || entry.currency.toUpperCase() !== g.currency.toUpperCase())
    return staticLine

  const pct = ((entry.price - grantPrice) / grantPrice) * 100
  const sign = pct >= 0 ? '+' : ''
  return `${staticLine} • now ${fmtGrantPrice(entry.price, entry.currency)} • ${sign}${pct.toFixed(1)}% since grant (unvested units)`
}

function buildGrant(g: Grant, priceByInstrument: Record<string, PriceEntry>): GrantVM {
  const sorted = [...g.tranches].sort((a, b) => a.vest_date.localeCompare(b.vest_date))
  const nextT = sorted.find((t) => !t.vested)
  const now = today()
  const hasOverdue = sorted.some((t) => !t.vested && t.vest_date <= now)
  const pct = g.total_units > 0 ? Math.round((g.vested_units / g.total_units) * 100) : 0

  let badgeLabel: string
  let badgeTone: GrantBadgeTone
  if (g.status === 'fully_vested') {
    badgeLabel = 'Fully vested'
    badgeTone = 'muted'
  } else if (g.tax_event === 'deferred') {
    badgeLabel = 'Deferred tax'
    badgeTone = 'warn'
  } else {
    badgeLabel = 'Active'
    badgeTone = 'neutral'
  }

  const tranches = sorted.map((t) => buildTranche(t, !!(nextT && t.id === nextT.id)))

  const sinceGrantLine = buildSinceGrantLine(g, priceByInstrument)

  return {
    id: g.id,
    symbol: g.symbol,
    grantDate: g.grant_date,
    grantPrice: g.grant_price_per_unit,
    currency: g.currency,
    total: g.total_units,
    vested: g.vested_units,
    unvested: g.unvested_units,
    nextVest: g.next_vest_date,
    hasOverdue,
    taxEvent: g.tax_event,
    status: g.status,
    badgeLabel,
    badgeTone,
    pct,
    tranches,
    sinceGrantLine,
  }
}

// Sort: active (with next_vest_date) ascending, then rest.
function sortGrants(vms: GrantVM[]): GrantVM[] {
  return [...vms].sort((a, b) => {
    const aa = a.status === 'active' && a.nextVest != null
    const bb = b.status === 'active' && b.nextVest != null
    if (aa && bb) return (a.nextVest ?? '') < (b.nextVest ?? '') ? -1 : 1
    if (aa) return -1
    if (bb) return 1
    return 0
  })
}

// ── Upcoming vest view model ──────────────────────────────────────────────────

export interface UpcomingVM {
  grantId: string
  trancheId: string
  symbol: string
  date: string
  units: number
  overdue: boolean
}

function buildUpcoming(items: UpcomingVestItem[]): UpcomingVM[] {
  return [...items]
    .sort((a, b) => a.vest_date.localeCompare(b.vest_date))
    .map((item) => ({
      grantId: item.grant_id,
      trancheId: item.tranche_id,
      symbol: item.symbol,
      date: item.vest_date,
      units: item.units,
      overdue: item.overdue,
    }))
}

// ── Persisted vest-event history view model ──────────────────────────────────

export interface VestHistoryVM {
  vestEventId: string
  grantId: string
  trancheId: string
  symbol: string
  date: string
  units: number
  releaseStatus: 'released' | 'awaiting_release' | 'not_released'
  releasedOn: string | null
  expectedReleaseDate: string | null
  overdue: boolean
  /** The existing release modal's input contract; only actionable while pending. */
  releaseItem: PendingReleaseItem | null
  /** Price per unit at vest time, in fmvCurrency. Declared non-null on VestHistoryItem, but a
   *  legacy vest event recorded before this field existed can still send it null over the wire
   *  — treat both as genuinely nullable rather than trust the API type. */
  fmvPerUnit: number | null
  fmvCurrency: string | null
}

export function buildVestHistory(items: VestHistoryItem[]): VestHistoryVM[] {
  return [...items]
    .sort((a, b) => b.vest_date.localeCompare(a.vest_date))
    .map((item) => ({
      vestEventId: item.vest_event_id,
      grantId: item.grant_id,
      trancheId: item.tranche_id,
      symbol: item.symbol,
      date: item.vest_date,
      units: item.units,
      releaseStatus: item.release_status,
      releasedOn: item.released_on,
      expectedReleaseDate: item.expected_release_date,
      overdue: item.overdue,
      releaseItem: item.release_status === 'released' ? null : item,
      fmvPerUnit: item.fmv_per_unit ?? null,
      fmvCurrency: item.fmv_currency ?? null,
    }))
}

// ── View model root ───────────────────────────────────────────────────────────

export interface EquityViewModel {
  loading: boolean
  error: boolean
  summaryCards: EquitySummaryCard[]
  grants: GrantVM[]
  rawGrants: Grant[]
  upcoming: UpcomingVM[]
  vestHistory: VestHistoryVM[]
  refetch: () => void
}

export function useEquityView(rows: ComputedRow[] = []): EquityViewModel {
  const cached = _cache.get(CACHE_KEY)
  const [grants, setGrants] = useState<Grant[]>(cached?.grants ?? [])
  const [upcoming, setUpcoming] = useState<UpcomingVestItem[]>(cached?.upcoming ?? [])
  const [vestHistory, setVestHistory] = useState<VestHistoryItem[]>(cached?.vestHistory ?? [])
  const [loading, setLoading] = useState(cached == null)
  const [error, setError] = useState(false)
  const [version, setVersion] = useState(0)

  const refetch = useCallback(() => {
    _cache.invalidate(CACHE_KEY)
    setVersion((v) => v + 1)
  }, [])

  // Grants, vests and releases are Lucius-writable — refetch in place after any write.
  const anyWriteV = useDataVersion('*')

  useEffect(() => {
    let alive = true
    const hit = _cache.get(CACHE_KEY)
    if (hit != null && version === 0) {
      setGrants(hit.grants)
      setUpcoming(hit.upcoming)
      setVestHistory(hit.vestHistory ?? [])
      setLoading(false)
      setError(false)
    } else {
      setLoading(true)
      setError(false)
    }

    Promise.all([
      authFetch('/api/equity/grants')
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((d: GrantsResponse) => d.grants ?? []),
      authFetch('/api/equity/upcoming')
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((d: UpcomingVestsResponse) => d.items ?? []),
      authFetch('/api/equity/vest-history')
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((d: VestHistoryResponse) => d.items ?? []),
    ])
      .then(([g, u, h]) => {
        if (!alive) return
        _cache.set(CACHE_KEY, { grants: g, upcoming: u, vestHistory: h })
        setGrants(g)
        setUpcoming(u)
        setVestHistory(h)
        setLoading(false)
      })
      .catch(() => {
        if (!alive) return
        setError(true)
        setLoading(false)
      })

    return () => {
      alive = false
    }
  }, [version, anyWriteV])

  const priceByInstrument = buildPriceByInstrument(rows)
  const grantVMs = sortGrants(grants.map((g) => buildGrant(g, priceByInstrument)))
  const upcomingVMs = buildUpcoming(upcoming)
  const vestHistoryVMs = buildVestHistory(vestHistory)
  const summaryCards = loading
    ? [
        { label: 'Total grants', value: '—' },
        { label: 'Vested units', value: '—' },
        { label: 'Unvested units', value: '—' },
        { label: 'Next vest', value: '—' },
      ]
    : buildSummaryCards(grants)

  return {
    loading,
    error,
    summaryCards,
    grants: grantVMs,
    rawGrants: grants,
    upcoming: upcomingVMs,
    vestHistory: vestHistoryVMs,
    refetch,
  }
}
