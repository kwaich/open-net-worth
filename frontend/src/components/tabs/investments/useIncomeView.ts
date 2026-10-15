import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import type {
  IncomeEvent,
  IncomeForecast,
  IncomeForecastRow,
  IncomeSummary,
} from '../../../types/portfolio'
import type { IncomeCalendar, IncomeCalendarRow } from '../../../types/income'
import { fetchIncome, fetchIncomeCalendar, fetchIncomeForecast } from '../../../api/portfolio'
import { fBase, fDate, fPct } from '../../../utils/format'
import type { StatCard } from '../../ui'
import { ViewCache } from './viewCache'

interface IncomeData {
  summary: IncomeSummary | null
  forecast: IncomeForecast | null
  calendar: IncomeCalendar | null
}

// Module-level cache — populated on first successful fetch and reused on re-mount.
const _cache = new ViewCache<IncomeData>('income')

// Date helpers — same logic as the IncomePanel shared component.
function daysUntil(iso: string): number {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const target = new Date(`${iso}T00:00:00`)
  return Math.round((target.getTime() - today.getTime()) / 86_400_000)
}

function fRelDays(n: number): string {
  if (n <= 0) return 'today'
  if (n === 1) return 'in 1 day'
  return `in ${n} days`
}

// ── Derived view shapes ───────────────────────────────────────────────────────

/** A source-list row for "Where your income comes from". */
export interface IncomeSourceRow {
  positionId: string
  sym: string
  name: string
  annualBase: number
  /** 0–100: proportion of the max contributor's annual income (bar width %). */
  barPct: number
  /** Source reliability bucket. */
  source: 'declared' | 'fixedIncome' | 'trailing'
  /** Dot color token, keyed by `source`. */
  dotColor: string
}

/** The "Is your income growing?" comparison panel. */
export interface IncomeGrowthView {
  received: number
  receivedFmt: string
  /** bar width % for received */
  receivedBarPct: number
  projected: number
  projectedFmt: string
  /** bar width % for projected */
  projectedBarPct: number
  /** Delta from received → projected, or null when received is 0. */
  growthPct: number | null
}

/** One upcoming payment row. */
export interface IncomePaymentRow {
  positionId: string
  sym: string
  name: string
  exDate: string
  payDate: string
  /** ISO date string: ex-dividend date */
  exIso: string
  /** Days until ex-dividend (negative = past) */
  exDaysUntil: number
  amountBase: number
  amountFmt: string
  status: 'scheduled' | 'estimated'
}

/** Next-payment hero derived from calendar.next_payment. */
export interface IncomeNextPayment {
  sym: string
  exLabel: string
  exRelDays: string
  payLabel: string
  amountFmt: number
}

/** One persisted payment that has already been received or reinvested. */
export interface IncomeHistoryRow {
  eventId: string
  date: string
  symbol: string
  name: string
  kindLabel: 'Dividend' | 'Interest' | 'Distribution'
  amountBase: number | null
  amountBaseAvailable: boolean
  amountNative: number
  amountCurrency: string
  deliveryLabel: 'Cash' | 'Reinvested'
}

export interface IncomeViewState {
  loading: boolean
  error: boolean

  hasForecast: boolean
  hasSummary: boolean
  hasCalendar: boolean
  hasAny: boolean

  /** KPI summary bar cards. */
  cards: StatCard[]

  /** Ranked by annual_income_base desc (highest contributor first). */
  sourceRows: IncomeSourceRow[]
  /** Rows beyond the top 8 collapsed into the "+N more" row. */
  restSrcCount: number
  restSrcSum: number
  restSrcBarPct: number
  totalSourceCount: number

  growth: IncomeGrowthView | null
  incomeBaseTotalsAvailable: boolean

  /** Next 90-day calendar. */
  calendarHorizonDays: number
  calendarCount: number
  calendarTotalFmt: string
  nextPayment: IncomeNextPayment | null
  paymentRows: IncomePaymentRow[]
  totalPaymentCount: number
  historyRows: IncomeHistoryRow[]
  allHistoryRows: IncomeHistoryRow[]
  totalHistoryCount: number
}

const SRC_TOP = 8
const PAY_TOP = 8
const HISTORY_TOP = 8

function bucketForSource(raw: IncomeForecastRow['source']): IncomeSourceRow['source'] {
  if (raw === 'declared') return 'declared'
  if (raw === 'coupon' || raw === 'accretion') return 'fixedIncome'
  return 'trailing' // covers 'trailing' and 'none'
}

const SOURCE_DOT: Record<IncomeSourceRow['source'], string> = {
  declared: 'var(--primary)',
  fixedIncome: 'var(--warn)',
  trailing: 'var(--muted)',
}

function toSourceRow(row: IncomeForecastRow, maxAnnual: number): IncomeSourceRow {
  const barPct = maxAnnual > 0 ? (row.annual_income_base / maxAnnual) * 100 : 0
  const source = bucketForSource(row.source)
  return {
    positionId: row.position_id,
    sym: row.symbol,
    name: row.name,
    annualBase: row.annual_income_base,
    barPct,
    source,
    dotColor: SOURCE_DOT[source],
  }
}

function toPaymentRow(p: IncomeCalendarRow): IncomePaymentRow {
  const exDaysUntil = daysUntil(p.ex_date)
  return {
    positionId: p.position_id,
    sym: p.symbol,
    name: p.name,
    exDate: fDate(p.ex_date),
    payDate: fDate(p.pay_date),
    exIso: p.ex_date,
    exDaysUntil,
    amountBase: p.amount_base,
    amountFmt: fBase(p.amount_base),
    status: p.status,
  }
}

const INCOME_KIND_LABELS: Record<IncomeEvent['income_kind'], IncomeHistoryRow['kindLabel']> = {
  dividend: 'Dividend',
  interest: 'Interest',
  distribution: 'Distribution',
}

function toHistoryRow(event: IncomeEvent): IncomeHistoryRow {
  const symbol = event.instrument_ticker || event.instrument_label || 'Unknown holding'
  const name =
    event.instrument_label && event.instrument_label !== symbol
      ? event.instrument_label
      : event.account_name || ''
  return {
    eventId: event.id,
    date: event.paid_at,
    symbol,
    name,
    kindLabel: INCOME_KIND_LABELS[event.income_kind],
    amountBase: event.base_amount,
    amountBaseAvailable: event.base_amount_available !== false,
    amountNative: event.native_net_amount ?? event.gross_amount - event.tax_withheld,
    amountCurrency: event.native_currency || event.currency || '',
    deliveryLabel: event.is_drp ? 'Reinvested' : 'Cash',
  }
}

export function buildIncomeHistory(events: IncomeEvent[]): IncomeHistoryRow[] {
  return [...events]
    .sort(
      (a, b) =>
        b.paid_at.localeCompare(a.paid_at) ||
        (b.created_at ?? '').localeCompare(a.created_at ?? '') ||
        b.id.localeCompare(a.id),
    )
    .map(toHistoryRow)
}

function buildCards(summary: IncomeSummary | null, forecast: IncomeForecast | null): StatCard[] {
  const cards: StatCard[] = []
  if (summary) {
    const totalsAvailable = summary.base_totals_available !== false
    cards.push({
      label: 'Received • last 12m',
      value: totalsAvailable ? fBase(summary.trailing_12m_base) : 'FX unavailable',
    })
    cards.push({
      label: 'Received • all time',
      value: totalsAvailable ? fBase(summary.total_base) : 'FX unavailable',
    })
    cards.push({
      label: 'Yield on cost',
      value:
        summary.yield_on_cost_available !== false && summary.yield_on_cost_pct != null
          ? fPct(summary.yield_on_cost_pct)
          : summary.yield_on_cost_available === false
            ? 'FX unavailable'
            : '—',
    })
  }
  if (forecast) {
    cards.push({ label: 'Forecast • next 12m', value: fBase(forecast.annual_total_base) })
    cards.push({
      label: 'Forward yield',
      value: forecast.forward_yield_pct != null ? fPct(forecast.forward_yield_pct) : '—',
    })
  }
  return cards
}

export function deriveIncomeView(
  summary: IncomeSummary | null,
  forecast: IncomeForecast | null,
  calendar: IncomeCalendar | null,
): Omit<IncomeViewState, 'loading' | 'error'> {
  const hasForecast = !!forecast && forecast.holdings.length > 0
  const hasSummary = !!summary && (summary.events.length > 0 || summary.total_base > 0)
  const hasCalendar = !!calendar && calendar.payments.length > 0
  const hasAny = hasForecast || hasSummary || hasCalendar

  const cards = buildCards(summary, forecast)

  // Source rows
  const ranked = forecast
    ? [...forecast.holdings].sort((a, b) => b.annual_income_base - a.annual_income_base)
    : []
  const maxAnnual = ranked.reduce((m, h) => Math.max(m, h.annual_income_base), 0)
  const allSourceRows = ranked.map((r) => toSourceRow(r, maxAnnual))
  const topSrc = allSourceRows.slice(0, SRC_TOP)
  const restSrc = allSourceRows.slice(SRC_TOP)
  const restSrcSum = restSrc.reduce((s, r) => s + r.annualBase, 0)
  const restSrcBarPct = maxAnnual > 0 ? (restSrcSum / maxAnnual) * 100 : 0

  // Growth
  const received = summary?.trailing_12m_base ?? 0
  const projected = forecast?.annual_total_base ?? 0
  const growMax = Math.max(received, projected) || 1
  const growthPct = received > 0 ? ((projected - received) / received) * 100 : null
  const growth: IncomeGrowthView | null =
    hasForecast && hasSummary && summary?.base_totals_available !== false
      ? {
          received,
          receivedFmt: fBase(received),
          receivedBarPct: (received / growMax) * 100,
          projected,
          projectedFmt: fBase(projected),
          projectedBarPct: (projected / growMax) * 100,
          growthPct,
        }
      : null

  // Calendar
  const allPayments = calendar ? calendar.payments.map(toPaymentRow) : []
  const next = calendar?.next_payment ?? null
  const nextPayment: IncomeNextPayment | null = next
    ? {
        sym: next.symbol,
        exLabel: fDate(next.ex_date),
        exRelDays: fRelDays(daysUntil(next.ex_date)),
        payLabel: fDate(next.pay_date),
        amountFmt: next.amount_base,
      }
    : null
  const allHistoryRows = summary ? buildIncomeHistory(summary.events) : []

  return {
    hasForecast,
    hasSummary,
    hasCalendar,
    hasAny,
    cards,
    sourceRows: topSrc,
    restSrcCount: restSrc.length,
    restSrcSum,
    restSrcBarPct,
    totalSourceCount: allSourceRows.length,
    growth,
    incomeBaseTotalsAvailable: summary?.base_totals_available !== false,
    calendarHorizonDays: calendar?.horizon_days ?? 90,
    calendarCount: calendar?.count ?? 0,
    calendarTotalFmt: calendar ? fBase(calendar.total_base) : '—',
    nextPayment,
    paymentRows: allPayments.slice(0, PAY_TOP),
    totalPaymentCount: allPayments.length,
    historyRows: allHistoryRows.slice(0, HISTORY_TOP),
    allHistoryRows,
    totalHistoryCount: allHistoryRows.length,
  }
}

/**
 * useIncomeView — fetches GET /api/income + /api/income/forecast + /api/income/calendar
 * and maps them to the exact view shape the IncomeTab component needs.
 *
 * `version` is bumped by the parent when a dividend is recorded so the panel refetches.
 */
const CACHE_KEY = 'income'

export function useIncomeView(version = 0): IncomeViewState & {
  showAllSrc: boolean
  setShowAllSrc: Dispatch<SetStateAction<boolean>>
  showAllSrc_allRows: IncomeSourceRow[]
  showAllPay: boolean
  setShowAllPay: Dispatch<SetStateAction<boolean>>
  allPaymentRows: IncomePaymentRow[]
  showAllHistory: boolean
  setShowAllHistory: Dispatch<SetStateAction<boolean>>
  retry: () => void
} {
  // When version changes (dividend recorded), clear the cache so the next
  // fetch always returns fresh data even on remount.
  const prevVersionRef = useRef(version)
  if (version !== prevVersionRef.current) {
    prevVersionRef.current = version
    _cache.invalidate(CACHE_KEY)
  }

  const cached = _cache.get(CACHE_KEY)
  const [summary, setSummary] = useState<IncomeSummary | null>(cached?.summary ?? null)
  const [forecast, setForecast] = useState<IncomeForecast | null>(cached?.forecast ?? null)
  const [calendar, setCalendar] = useState<IncomeCalendar | null>(cached?.calendar ?? null)
  const [loading, setLoading] = useState(cached == null)
  const [error, setError] = useState(false)
  const [retryKey, setRetryKey] = useState(0)

  const [showAllSrc, setShowAllSrc] = useState(false)
  const [showAllPay, setShowAllPay] = useState(false)
  const [showAllHistory, setShowAllHistory] = useState(false)

  useEffect(() => {
    let alive = true
    const hit = _cache.get(CACHE_KEY)
    if (hit != null) {
      setSummary(hit.summary)
      setForecast(hit.forecast)
      setCalendar(hit.calendar)
      setLoading(false)
      setError(false)
    } else {
      setLoading(true)
      setError(false)
    }
    Promise.all([fetchIncome(), fetchIncomeForecast(12), fetchIncomeCalendar(90)])
      .then(([s, f, c]) => {
        if (!alive) return
        _cache.set(CACHE_KEY, { summary: s, forecast: f, calendar: c })
        setSummary(s)
        setForecast(f)
        setCalendar(c)
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
  }, [version, retryKey])

  const derived = useMemo(
    () => deriveIncomeView(summary, forecast, calendar),
    [summary, forecast, calendar],
  )

  // For the "show all" source rows expansion
  const ranked = forecast
    ? [...forecast.holdings].sort((a, b) => b.annual_income_base - a.annual_income_base)
    : []
  const maxAnnual = ranked.reduce((m, h) => Math.max(m, h.annual_income_base), 0)
  const showAllSrc_allRows = useMemo(
    () => ranked.map((r) => toSourceRow(r, maxAnnual)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [forecast],
  )

  const allPaymentRows = useMemo(
    () => (calendar ? calendar.payments.map(toPaymentRow) : []),
    [calendar],
  )

  return {
    loading,
    error,
    ...derived,
    showAllSrc,
    setShowAllSrc,
    showAllSrc_allRows,
    showAllPay,
    setShowAllPay,
    allPaymentRows,
    showAllHistory,
    setShowAllHistory,
    retry: () => setRetryKey((k) => k + 1),
  }
}
