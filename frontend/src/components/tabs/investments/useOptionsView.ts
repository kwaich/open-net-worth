import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { useDataVersion } from '../../../hooks/useDataVersion'
import { fDate } from '../../../utils/format'
import { ViewCache } from './viewCache'

// Module-level cache — single entry, keyed 'options'.
const _cache = new ViewCache<OptionsResponse>('options')
const CACHE_KEY = 'options'

// ── Contract shape returned by GET /api/options (lane/fix-options-contract) ──

export interface OptionPosition {
  id: string
  underlying_symbol: string
  option_type: 'CALL' | 'PUT'
  side: 'long' | 'short'
  contracts: number
  strike: number
  expiry: string
  dte: number
  /** Net premium in base currency — positive = collected (short), negative = paid (long) */
  premium: number
  /** Current mark value in base currency — open P&L contribution */
  mark: number
  status: 'ITM' | 'OTM'
  /** Grouping: "income" = covered calls/CSPs, "hedge" = protective puts/long calls */
  strategy_group: 'income' | 'hedge'
  /** Cash collateral or margin committed, base currency */
  collateral: number
  note: string
  strategy_tag: string
}

export interface OptionsSummary {
  open_positions: number
  premium_collected: number
  capital_committed: number
  nearest_expiry_date: string | null
  nearest_expiry_dte: number | null
  nearest_expiry_underlying: string | null
}

export interface OptionsResponse {
  base_currency: string
  summary: OptionsSummary
  positions: OptionPosition[]
}

// ── View model (what the component renders) ──

export interface OptionsSummaryCard {
  label: string
  value: string
  caption?: string
}

export interface OptionsViewModel {
  baseCurrency: string
  summaryCards: OptionsSummaryCard[]
  incomePositions: OptionPosition[]
  hedgePositions: OptionPosition[]
  netMark: number
  loading: boolean
  error: boolean
  refetch: () => void
}

function formatAmount(n: number, currency: string): string {
  return (
    new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      maximumFractionDigits: 0,
    }).format(Math.round(Math.abs(n))) ?? ''
  )
}

function buildSummaryCards(data: OptionsResponse): OptionsSummaryCard[] {
  const { summary, base_currency: ccy } = data
  const nearestValue = summary.nearest_expiry_date ? fDate(summary.nearest_expiry_date) : '—'
  const nearestCaption =
    summary.nearest_expiry_dte != null && summary.nearest_expiry_underlying
      ? `${summary.nearest_expiry_dte}d • ${summary.nearest_expiry_underlying}`
      : undefined
  return [
    { label: 'Open positions', value: String(summary.open_positions) },
    { label: 'Premium collected', value: formatAmount(summary.premium_collected, ccy) },
    { label: 'Capital committed', value: formatAmount(summary.capital_committed, ccy) },
    { label: 'Nearest expiry', value: nearestValue, caption: nearestCaption },
  ]
}

export function useOptionsView(): OptionsViewModel {
  const [data, setData] = useState<OptionsResponse | null>(() => _cache.get(CACHE_KEY) ?? null)
  const [loading, setLoading] = useState(() => _cache.get(CACHE_KEY) == null)
  const [error, setError] = useState(false)
  const [version, setVersion] = useState(0)

  const refetch = useCallback(() => {
    _cache.invalidate(CACHE_KEY)
    setVersion((v) => v + 1)
  }, [])

  // Lucius write tools tag mutates=("options",…) → data_changed SSE → refetch.
  const optionsDomainV = useDataVersion('options')
  useEffect(() => {
    if (optionsDomainV > 0) refetch()
  }, [optionsDomainV, refetch])

  useEffect(() => {
    let alive = true
    const cached = _cache.get(CACHE_KEY)
    if (cached != null && version === 0) {
      setData(cached)
      setLoading(false)
      setError(false)
    } else {
      setLoading(true)
      setError(false)
    }
    authFetch('/api/options')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: OptionsResponse) => {
        if (!alive) return
        _cache.set(CACHE_KEY, d)
        setData(d)
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
  }, [version])

  if (loading || error || !data) {
    return {
      baseCurrency: 'SGD',
      summaryCards: [],
      incomePositions: [],
      hedgePositions: [],
      netMark: 0,
      loading,
      error,
      refetch,
    }
  }

  const positions = data.positions
  return {
    baseCurrency: data.base_currency,
    summaryCards: buildSummaryCards(data),
    incomePositions: positions
      .filter((p) => p.strategy_group === 'income')
      .sort((a, b) => a.dte - b.dte),
    hedgePositions: positions
      .filter((p) => p.strategy_group === 'hedge')
      .sort((a, b) => a.dte - b.dte),
    netMark: positions.reduce((s, p) => s + p.mark, 0),
    loading: false,
    error: false,
    refetch,
  }
}
