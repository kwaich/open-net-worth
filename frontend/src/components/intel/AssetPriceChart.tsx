import { useEffect, useMemo, useRef, useState } from 'react'
import { LineStyle, type AutoscaleInfo, type MouseEventParams, type Time } from 'lightweight-charts'
import { authFetch } from '../../api/client'
import { Button, EmptyState, Loading, Modal, SegmentedControl } from '../ui'
import { Maximize2 } from '../../ui/icons'
import type {
  AssetPriceSeries,
  MarketDataState,
  MarketStatus,
  SeriesTimeframe,
} from '../../types/assetDetail'
import { buildIntradayTape, chartEmptyReason, type TailPoint } from './priceTail'
import { DRAG_IGNORE_PROPS } from '../../hooks/useSheetDragToDismiss'
import { ChartTypeMenu, ToolbarMenu, readStoredChartType, type ChartType } from './ChartTypeMenu'
import {
  buildChart,
  buildFixedRangeGrid,
  capAutoscaleMax,
  edgeToEdgeLogicalRange,
  formatAxisPrice,
  mainSeriesColorOptions,
  mainSeriesLastValueOptions,
  niceTicks,
  ohlcOf,
  seriesBaseline,
  shouldShowPrevCloseLine,
  tipPlacement,
  toTime,
  type ChartHandle,
  usableSessionWindow,
  volumeAutoscaleCap,
  widenAutoscaleToPrice,
} from './assetPriceChartLwc'
import { fPrice, fNum, timeAgo, getDateLocale, priceDigits } from '../../utils/format'
import { useTheme } from '../../theme'

// Session cache of fetched series, keyed by `${instrumentId}:${timeframe}:${currency}`. Lets a
// reopen (or a revisited timeframe) render instantly and revalidate in the background,
// instead of flashing the chart spinner every time.
const seriesCache = new Map<string, AssetPriceSeries>()

function seriesState(series: AssetPriceSeries): MarketDataState {
  // Existing equity responses predate the explicit server state; preserving their drawable
  // behavior avoids treating a healthy legacy payload as an outage.
  return series.state ?? 'live'
}

function shouldCacheSeries(series: AssetPriceSeries): boolean {
  return seriesState(series) !== 'unavailable' && series.points.length >= 2
}

function cacheSeries(key: string, next: AssetPriceSeries): void {
  if (seriesState(next) === 'unavailable') {
    seriesCache.delete(key)
    return
  }
  if (!shouldCacheSeries(next)) return

  const current = seriesCache.get(key)
  const nextAt = next.as_of ? new Date(next.as_of).getTime() : NaN
  const currentAt = current?.as_of ? new Date(current.as_of).getTime() : NaN
  if (Number.isFinite(nextAt) && Number.isFinite(currentAt) && nextAt < currentAt) return
  seriesCache.set(key, next)
}

function isOlderSeries(next: AssetPriceSeries, current: AssetPriceSeries | null): boolean {
  const nextAt = next.as_of ? new Date(next.as_of).getTime() : NaN
  const currentAt = current?.as_of ? new Date(current.as_of).getTime() : NaN
  return Number.isFinite(nextAt) && Number.isFinite(currentAt) && nextAt < currentAt
}

function relativeUpdatedAt(asOf: string): string {
  const at = new Date(asOf).getTime()
  return Number.isFinite(at) && at > Date.now() ? 'just now' : timeAgo(asOf)
}

function isPreviousSessionSeries(series: AssetPriceSeries | null): boolean {
  return !!series && series.degraded_reason === 'previous_session'
}

const TIMEFRAMES: { value: SeriesTimeframe; label: string }[] = [
  { value: '1D', label: '1D' },
  { value: '5D', label: '5D' },
  { value: '1M', label: '1M' },
  { value: '6M', label: '6M' },
  { value: 'YTD', label: 'YTD' },
  { value: '1Y', label: '1Y' },
  { value: '5Y', label: '5Y' },
  { value: 'MAX', label: 'MAX' },
]

/** Read a CSS custom property from :root at chart-creation time.
 *  lightweight-charts (like Chart.js before it) draws on canvas and cannot consume
 *  CSS vars directly. */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

interface HoverInfo {
  dateLabel: string
  open: number
  high: number
  low: number
  close: number
  volume: number | null
  /** Crosshair position + pane size at event time — the tooltip follows the cursor,
   *  flipping sides past the pane midpoint (see tipPlacement). */
  x: number
  y: number
  paneW: number
  paneH: number
}

function formatHoverDate(
  dateStr: string,
  timeframe: SeriesTimeframe,
  timezone?: string | null,
): string {
  const d = new Date(dateStr)
  const intraday = timeframe === '1D' || timeframe === '5D'
  // getDateLocale() (the user's own date_format preference, same source fDate/fTime read),
  // not `undefined` (the browser's own locale) — Intl picks 12h/AM-PM vs 24h FROM that
  // locale, so this follows the user's own setting instead of always defaulting to
  // whatever the browser happens to be configured as (owner ask 2026-09-01).
  //
  // Intraday matches the axis/crosshair's exchange-timezone rendering below (a 1D/5D chart
  // is about that exchange's own session) — `timeZone` is a plain top-level option, safe to
  // combine with dateStyle/timeStyle (only `timeZoneName` cannot be, the earlier crash here).
  return intraday
    ? d.toLocaleString(getDateLocale(), {
        dateStyle: 'medium',
        timeStyle: 'short',
        timeZone: timezone ?? undefined,
      })
    : d.toLocaleDateString(getDateLocale(), { dateStyle: 'medium' })
}

interface Props {
  instrumentId: string
  currency: string
  /** Asset detail passes the real class; direct legacy consumers retain equity semantics. */
  assetClass?: string
  /** Live quote from the detail header (same fetch_live source) — appended as the
   *  intraday tape's trailing point so the chart's right edge tracks "now". */
  livePrice?: number | null
  liveAt?: string | null
  /** Prior close — used as the Baseline chart's reference value, seeds a
   *  prev-close -> live 2-point line while an open market's delayed feed has no
   *  drawable bars yet (see `buildIntradayTape`), and is the 1D comparator the
   *  up/down line color agrees with the detail header's % change against (see
   *  `seriesBaseline`) — ALWAYS pass the real value even when showPrevCloseLine is
   *  false; only the visible annotation is asset-type-specific, not this math. */
  prevClose?: number | null
  /** Whether to draw the dashed "Prev close" reference line + axis label (matching
   *  Yahoo's own convention). Meaningful for a stock (an actual closing print) but
   *  not a 24/7 asset with no closing bell — default true, set false for crypto. */
  showPrevCloseLine?: boolean
  /** Whether the exchange is currently open — when a 1D chart can't draw while open, the
   *  vendor feed is merely delayed (say so) rather than us lacking history. */
  marketOpen?: boolean
  /** Today's REAL pre/regular/post start+end from Yahoo's own currentTradingPeriod (see
   *  `MarketStatus.session_bounds`), present only while a pre/post-market session is active
   *  AND Yahoo gives this venue real extended-hours data. Pins the 1D chart's visible range
   *  to the full session window (see `sessionWindowRange`) instead of auto-fitting to
   *  whatever partial tape has arrived so far, which read as the whole day having already
   *  happened 45 minutes into pre-market (owner ask 2026-08-31 — explicitly NOT a hardcoded
   *  local-hour guess). Null/absent keeps the existing fit-to-data behavior — every other
   *  exchange/timeframe is unchanged. */
  sessionBounds?: MarketStatus['session_bounds']
  /** IANA zone the exchange trades in (`price.market.timezone`) — the 1D/5D axis, crosshair,
   *  and hover tooltip render in THIS zone rather than the viewer's, since a fixed intraday
   *  session window is inherently about the exchange's own clock (see `tickMarkFormatterFor`
   *  in assetPriceChartLwc.ts). Null/undefined falls back to the viewer's local time. */
  timezone?: string | null
  /** Reports the 1D tape's own trailing point (real bar or appended live tail, whichever
   *  ended up last — see `buildIntradayTape`) whenever it changes. The detail header's own
   *  live quote and this chart's intraday bars are two INDEPENDENT Yahoo feeds that don't
   *  update in lockstep (owner-reproduced 2026-09-01: the header briefly showed a NEWER
   *  price than the chart's last bar, and separately the reverse) — the header uses this to
   *  show whichever of the two is actually freshest, instead of trusting its own live quote
   *  unconditionally. Fires only for the '1D' timeframe; other timeframes aren't "now". */
  onLatestPoint?: (price: number, at: string) => void
}

export function AssetPriceChart({
  instrumentId,
  currency,
  assetClass = 'equity',
  livePrice,
  liveAt,
  prevClose,
  showPrevCloseLine = true,
  marketOpen = false,
  sessionBounds = null,
  timezone = null,
  onLatestPoint,
}: Props) {
  // A callback ref (state, not a plain ref object) so the chart-lifecycle effect below
  // can depend on container PRESENCE directly — it re-runs exactly when the box mounts
  // (loading/empty-state -> chart) or unmounts (chart -> loading/empty-state), which a
  // plain useRef can't express as an effect dependency.
  const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null)
  // The resolved theme name — a dependency of the recolour below, so a light→dark switch
  // repaints the EXISTING chart (owner-reproduced 2026-09-20: the gridlines stayed the light
  // theme's colour after switching; every colour here was read once at chart creation).
  const { resolved: themeName } = useTheme()
  const handleRef = useRef<ChartHandle | null>(null)
  const pointsByTimeRef = useRef<Map<number, TailPoint>>(new Map())
  const timeframeRef = useRef<SeriesTimeframe>('1D')
  const [timeframe, setTimeframe] = useState<SeriesTimeframe>('1D')
  const [chartType, setChartType] = useState<ChartType>(() => readStoredChartType())
  // Fullscreen = the interactive mode: the same chart card renders inside a workspace
  // Modal with the library's native drag-pan + wheel/pinch zoom enabled. The inline
  // card stays the fitted, non-interactive read-out it always was. Toggling moves the
  // card's DOM subtree, so the container remounts and the chart rebuilds with the
  // right interaction wiring — series/timeframe/type state lives here and survives.
  const [fullscreen, setFullscreen] = useState(false)
  // In fullscreen the viewport belongs to the user — fit once per fresh chart and
  // once per TIMEFRAME switch (a new range in an old viewport is meaningless), never
  // on the 60s poll, or panning would snap back to the fitted view mid-inspection.
  // Holds the timeframe the current chart instance was last fitted for; nulled on
  // every chart rebuild.
  const fittedForRef = useRef<SeriesTimeframe | null>(null)
  // Real pre/regular/post boundaries (epoch seconds) for the dotted extended-hours overlay
  // below — set from `sessionBounds` on every data update, read back by
  // `updateChartOverlays` (which needs the SAME values on a bare resize/range-change
  // callback, where the prop itself isn't in scope).
  const sessionSegmentsRef = useRef<{
    preStart: number
    regStart: number
    regEnd: number
    postEnd: number
    hasPre: boolean
    hasPost: boolean
  } | null>(null)
  // Pixel bands (left/width, relative to the chart pane) for the dotted "pre/post-market
  // hours" texture — Perplexity/Yahoo-style, owner ask 2026-08-31. Marks WHICH HOURS are
  // outside the regular session, not which hours have already elapsed (owner correction,
  // 2026-08-31: "the dot is for pre vs post, not for happened vs not happened") — so this
  // renders REGARDLESS of where "now" falls, and the regular-session band in between is
  // always left plain. Recomputed on every data update AND on any lightweight-charts-driven
  // range change (resize, autoSize) via subscribeVisibleLogicalRangeChange below, since it
  // lives outside the canvas the library itself draws.
  // `width` undefined means "extend to the pane's right edge" (the post-market band, whose
  // own end coordinate can fail to resolve — see updateChartOverlays).
  const [extendedHourBands, setExtendedHourBands] = useState<{ left: number; width?: number }[]>([])
  // Custom left-edge price labels + the "Prev close" pill (owner ask 2026-09-21: no reserved
  // right-side axis gutter — the plot runs full-bleed, "like Perplexity"; see
  // assetPriceChartLwc's `rightPriceScale: { visible: false }`). Read back by
  // `updateChartOverlays` on the SAME bare resize/range-change callback the extended-hours
  // bands use, where the render's own `prevClose`/`priceMaxFrac` aren't in scope — same
  // ref-relay pattern as `sessionSegmentsRef` above, refreshed every data update.
  const axisContextRef = useRef<{
    prevClose: number | null
    showPrevClose: boolean
    maxFrac: number | undefined
    ccy: string
  }>({ prevClose: null, showPrevClose: false, maxFrac: undefined, ccy: '' })
  const [axisTicks, setAxisTicks] = useState<{ y: number; label: string }[]>([])
  const [prevCloseTag, setPrevCloseTag] = useState<{ y: number; label: string } | null>(null)
  const [hover, setHover] = useState<HoverInfo | null>(null)
  const initialSeries = seriesCache.get(`${instrumentId}:1D:${currency}`) ?? null
  const [series, setSeries] = useState<AssetPriceSeries | null>(initialSeries)
  const visibleSeriesRef = useRef<AssetPriceSeries | null>(initialSeries)
  const requestSequenceRef = useRef(0)
  const latestAppliedSequenceRef = useRef(0)
  const [loading, setLoading] = useState(() => !seriesCache.has(`${instrumentId}:1D:${currency}`))
  const [loadError, setLoadError] = useState(false)
  const [retryVersion, setRetryVersion] = useState(0)

  const setVisibleSeries = (next: AssetPriceSeries | null) => {
    visibleSeriesRef.current = next
    setSeries(next)
  }

  useEffect(() => {
    let alive = true
    // Key by currency too: the same coin charted as BTC/USD and BTC/SGD are different series.
    const key = `${instrumentId}:${timeframe}:${currency}`
    const cached = seriesCache.get(key)

    // Cached (a reopen or a revisited timeframe): show it instantly and revalidate
    // quietly in the background — no spinner. Otherwise show the spinner while loading.
    if (cached) {
      setVisibleSeries(cached)
      setLoading(false)
    } else {
      setVisibleSeries(null)
      setLoading(true)
    }
    setLoadError(false)

    // `quiet` (the 60s poll, or a background revalidate of cached data) skips the
    // loading state so the chart updates in place instead of flashing the spinner,
    // and keeps the last-good series on a failure.
    const fetchSeries = (quiet: boolean) => {
      const requestSequence = ++requestSequenceRef.current
      if (!quiet) setLoading(true)
      const vsParam = currency ? `&vs=${encodeURIComponent(currency)}` : ''
      authFetch(`/api/intel/assets/${instrumentId}/series?timeframe=${timeframe}${vsParam}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((d: AssetPriceSeries) => {
          if (!alive || requestSequence < latestAppliedSequenceRef.current) return
          // A successful server response becomes authoritative only when it is applied. A
          // transport failure has no server authority, so it must not prevent an older
          // in-flight response from completing the initial load. Explicit unavailability
          // applies regardless of timestamps and evicts the cache immediately.
          const applies =
            seriesState(d) === 'unavailable' || !isOlderSeries(d, visibleSeriesRef.current)
          if (applies) {
            setVisibleSeries(d)
            cacheSeries(key, d)
            latestAppliedSequenceRef.current = requestSequence
          }
          setLoading(false)
        })
        .catch(() => {
          if (!alive || requestSequence < latestAppliedSequenceRef.current || quiet) return
          setLoading(false)
          setLoadError(!cached)
        })
    }

    fetchSeries(!!cached)

    // Near-real-time only for the 1D intraday view — the backend caches that series
    // for 60s; longer timeframes are daily+ bars that don't move minute-to-minute.
    // Paused while the tab is hidden (no point redrawing an unseen chart); a
    // visibilitychange tick catches up the moment the tab is shown again (mirrors
    // useAssetDetail.ts's own poll) — otherwise a transient fetch failure/empty
    // response that lands while the tab is hidden leaves the chart stuck on that
    // stale result until the tab happens to be visible on a later 60s tick, which can
    // be a long wait if the tab was backgrounded across the failure.
    let timer: ReturnType<typeof setInterval> | undefined
    let tick: (() => void) | undefined
    if (timeframe === '1D') {
      tick = () => {
        if (!document.hidden) fetchSeries(true)
      }
      timer = setInterval(tick, 60_000)
      document.addEventListener('visibilitychange', tick)
    }

    return () => {
      alive = false
      if (timer) clearInterval(timer)
      if (tick) document.removeEventListener('visibilitychange', tick)
    }
  }, [instrumentId, timeframe, currency, retryVersion])

  // Extend the intraday tape to the live quote so the right edge is "now" (tracks the
  // header's 60s price poll), then — while the market is OPEN but the delayed vendor feed
  // hasn't produced 2 drawable points yet — seed a prev-close -> live 2-point line so the
  // chart draws from the first second of the session (see `buildIntradayTape`). The live
  // quote is in the asset's quote currency, so only append it when the series is in that
  // same currency — never graft a USD point onto an SGD line. The seed is dated at the
  // session open (the fixed 1D grid's own `from`, see `sessionWindowRange`) so it lands in
  // the grid's first slot rather than before it. Memoized once per render (instead of
  // recomputed separately for the effect and the empty-state calc below) — both need the
  // exact same live-extended/seeded tape.
  const liveMatches = !series || series.currency === currency
  const points = useMemo(() => {
    const open =
      timeframe === '1D'
        ? usableSessionWindow(
            sessionBounds,
            series?.points ?? [],
            liveMatches ? (liveAt ?? null) : null,
          )
        : null
    return buildIntradayTape(
      series?.points ?? [],
      timeframe,
      liveMatches ? livePrice : null,
      liveMatches ? liveAt : null,
      Date.now(),
      liveMatches ? prevClose : null,
      marketOpen,
      assetClass !== 'crypto',
      open ? new Date(open.from * 1000).toISOString() : null,
    )
  }, [
    series,
    timeframe,
    liveMatches,
    livePrice,
    liveAt,
    prevClose,
    marketOpen,
    assetClass,
    sessionBounds,
  ])

  useEffect(() => {
    if (!onLatestPoint || timeframe !== '1D') return
    const last = points[points.length - 1]
    if (!last) return
    onLatestPoint(last.price, last.date)
  }, [onLatestPoint, timeframe, points])

  const ohlcAvailable = series?.ohlc_available ?? true
  const needsOhlc = chartType === 'candle' || chartType === 'bar'
  // Graceful degradation (design decision 6): a persisted Candle/Bar pick that outruns
  // the current timeframe/asset's OHLC coverage falls back to Line for THIS render —
  // the stored preference is untouched, so it resumes the moment OHLC is available again.
  const effectiveChartType: ChartType = needsOhlc && !ohlcAvailable ? 'line' : chartType

  timeframeRef.current = timeframe

  // Recomputes every pixel-coordinate-derived overlay: the pre/post-market dotted bands, the
  // custom left-edge price labels, and the "Prev close" pill — the three things that live
  // OUTSIDE the canvas lightweight-charts itself draws, so nothing re-renders them on its own
  // whenever the chart's internal layout changes. Read fresh on every call rather than
  // memoized: lightweight-charts' own coordinate mapping is what changes on resize/zoom, not
  // any React-visible prop — everything this reads comes off a ref for exactly that reason
  // (see sessionSegmentsRef's and axisContextRef's own docs).
  const updateChartOverlays = () => {
    const handle = handleRef.current
    if (!handle) {
      setExtendedHourBands([])
      setAxisTicks([])
      setPrevCloseTag(null)
      return
    }

    // Pixel bands for the pre-market and post-market segments — [] when there's no fixed
    // session window, when only regular bounds are known (nothing extended to mark), or the
    // time scale can't resolve coordinates yet (mid-layout).
    const seg = sessionSegmentsRef.current
    if (!seg) {
      setExtendedHourBands([])
    } else {
      const ts = handle.chart.timeScale()
      const preStartX = ts.timeToCoordinate(seg.preStart as Time)
      const regStartX = ts.timeToCoordinate(seg.regStart as Time)
      const regEndX = ts.timeToCoordinate(seg.regEnd as Time)
      const bands: { left: number; width?: number }[] = []
      if (seg.hasPre && preStartX != null && regStartX != null && regStartX > preStartX) {
        bands.push({ left: preStartX, width: regStartX - preStartX })
      }
      if (seg.hasPost && regEndX != null) {
        // Always anchored to the pane's real right edge (`right: 0`, no computed width) —
        // the post-market band spans to the END of the chart, which is the box's TRUE right
        // edge (owner ask 2026-09-21: "full edge... to the true edge"). postEnd's own
        // coordinate (the fixed-window whitespace tail's final bar,
        // timeToCoordinate(seg.postEnd)) is NOT a substitute for that: setVisibleLogicalRange
        // pins the last bar's CENTER to the timeScale's edge, which can resolve to a REAL
        // coordinate a few px short of the box's true edge rather than failing outright —
        // reproduced live, 2026-09-21. Using it as the band's WIDTH left a real gap of
        // un-dotted space at the box's true edge.
        bands.push({ left: regEndX })
      }
      setExtendedHourBands(bands)
    }

    // Custom left-edge price labels — the native right price scale is hidden entirely
    // (assetPriceChartLwc's `rightPriceScale: { visible: false }`, owner ask 2026-09-21: no
    // reserved axis gutter, the plot runs full-bleed "like Perplexity"), so there is no
    // library-drawn label to read back; niceTicks re-derives the same kind of round values
    // from the SAME visible-range API the (now invisible) axis would have used.
    const { prevClose, showPrevClose, maxFrac, ccy } = axisContextRef.current
    const range = handle.mainSeries.priceScale().getVisibleRange()
    if (!range) {
      setAxisTicks([])
    } else {
      const ticks: { y: number; label: string }[] = []
      for (const value of niceTicks(range.from, range.to, 5)) {
        const y = handle.mainSeries.priceToCoordinate(value)
        if (y != null) ticks.push({ y, label: formatAxisPrice(value, maxFrac) })
      }
      setAxisTicks(ticks)
    }

    // The "Prev close" pill — replaces the native price-line axis label (createPriceLine's
    // own `axisLabelVisible`, which has no gutter to draw into anymore) with a custom chip at
    // the same y, floating over the right edge — Perplexity's own "Prev close: $70.87" layout.
    // Carries the instrument's OWN currency code (matching this component's existing hover-
    // tooltip convention, `{ccy} {value}`) rather than a hardcoded "$" — wrong for anything
    // not USD.
    if (!showPrevClose || prevClose == null) {
      setPrevCloseTag(null)
    } else {
      const y = handle.mainSeries.priceToCoordinate(prevClose)
      setPrevCloseTag(
        y == null
          ? null
          : { y, label: `Prev close: ${ccy} ${formatAxisPrice(prevClose, maxFrac)}` },
      )
    }
  }

  // Chart LIFECYCLE — create exactly when the container appears or the chart type
  // changes (a different series kind needs a fresh chart instance), destroy the SAME
  // instance in this effect's own returned cleanup. Creating in one effect and
  // destroying in a separate unmount-only effect (the previous shape) survives a real
  // single mount but leaks/detaches under React 18 StrictMode's dev-only
  // mount -> cleanup -> mount pass, which exists to catch exactly that split.
  useEffect(() => {
    if (!containerEl) return
    const goodColor = cssVar('--good')
    const badColor = cssVar('--bad')
    const mutedColor = cssVar('--muted')
    const gridColor = cssVar('--surface2')
    const fontFamily = getComputedStyle(containerEl).fontFamily
    const timeVisible = true // corrected to the real span by the data-update effect below
    // Measured once, at creation, same as fontFamily above — this container isn't recreated
    // on a later resize (autoSize handles that internally), so this is a snapshot of "how
    // wide was the chart when it first mounted here", not a live subscription. Good enough:
    // width classes (mobile vs desktop) rarely change mid-session for the SAME container.
    const fontSize = containerEl.clientWidth < 380 ? 9 : undefined
    // Same rule as the price header's own cap (AssetDetailView's priceMaxFrac) — crypto
    // keeps real sub-cent precision, an equity's 3rd/4th decimal is usually just an
    // FX-converted artifact.
    const priceMaxFrac = assetClass === 'crypto' ? undefined : 2

    const handle = buildChart(
      containerEl,
      effectiveChartType,
      {
        goodColor,
        badColor,
        mutedColor,
        gridColor,
        lineColor: goodColor,
        timeVisible,
        fontFamily,
        fontSize,
        priceMaxFrac,
        timezone: timezone ?? undefined,
        isIntraday: () => timeframeRef.current === '1D',
      },
      fullscreen,
    )
    fittedForRef.current = null
    handle.chart.subscribeCrosshairMove((param: MouseEventParams<Time>) => {
      if (param.point == null || param.time == null) {
        setHover(null)
        return
      }
      const p = pointsByTimeRef.current.get(param.time as number)
      if (!p) {
        setHover(null)
        return
      }
      const ohlc = ohlcOf(p)
      setHover({
        dateLabel: p.label ?? formatHoverDate(p.date, timeframeRef.current, timezone),
        ...ohlc,
        volume: p.volume ?? null,
        x: param.point.x,
        y: param.point.y,
        paneW: containerEl.clientWidth,
        paneH: containerEl.clientHeight,
      })
    })
    handleRef.current = handle
    // Keeps the dotted pre/post-market bands' pixel positions synced across resize/autoSize/
    // pan/zoom — TWO subscriptions, not one, because they cover different cases:
    //  - subscribeVisibleLogicalRangeChange fires on pan/zoom (the visible BAR indices change).
    //  - subscribeSizeChange fires on a pure pixel-width resize — the case
    //    lockVisibleTimeRangeOnResize (buildChartOptions) exists FOR: it keeps the same bar
    //    indices visible across a resize, which means the logical range VALUE never changes,
    //    so subscribeVisibleLogicalRangeChange alone never fires here (owner-reproduced live
    //    bug, 2026-09-20: "why don't I see the dots anymore" — the right-rail cards (Analyst
    //    Consensus/Key Facts) finish loading and load-shift the chart column's width AFTER the
    //    chart already mounted at its own first, narrower layout pass; with only the logical-
    //    range subscription, the bands stayed pinned to that stale, since-resized width).
    handle.chart.timeScale().subscribeVisibleLogicalRangeChange(updateChartOverlays)
    handle.chart.timeScale().subscribeSizeChange(updateChartOverlays)

    return () => {
      handle.chart.timeScale().unsubscribeVisibleLogicalRangeChange(updateChartOverlays)
      handle.chart.timeScale().unsubscribeSizeChange(updateChartOverlays)
      handle.chart.remove()
      if (handleRef.current === handle) handleRef.current = null
    }
  }, [containerEl, effectiveChartType, fullscreen, timezone])

  // Chart DATA/theme updates — mutates the EXISTING instance via setData/applyOptions,
  // never creates or destroys it. Runs on every series/poll/timeframe change (including
  // the 60s intraday poll) without rebuilding, so the chart never flickers. `containerEl`
  // is included ONLY so this effect also re-runs the instant the lifecycle effect creates
  // a fresh (data-less) chart when the box first mounts — without it, a container
  // appearing with no OTHER dependency change (e.g. the loading spinner finishing while
  // points/timeframe/prevClose were already stable) left the new chart permanently blank,
  // since nothing would ever call setData on it again until some other dep changed.
  useEffect(() => {
    const handle = handleRef.current
    if (!handle || points.length < 2) return

    const firstVal = points[0]?.price ?? 0
    const lastVal = points[points.length - 1]?.price ?? 0
    const isUp = lastVal >= seriesBaseline(timeframe, firstVal, prevClose)

    const goodColor = cssVar('--good')
    const badColor = cssVar('--bad')
    const mutedColor = cssVar('--muted')
    const gridColor = cssVar('--surface2')
    const lineColor = isUp ? goodColor : badColor

    pointsByTimeRef.current = new Map(points.map((p) => [toTime(p.date), p]))

    const spanMs =
      new Date(points[points.length - 1]!.date).getTime() - new Date(points[0]!.date).getTime()
    const timeVisible = spanMs < 2 * 86_400_000
    // Layout/grid colours re-applied on every run (cheap, idempotent) — this effect also runs
    // on a theme switch (`themeName` below), which is the only way those two ever change.
    handle.chart.applyOptions({
      timeScale: { timeVisible },
      layout: { textColor: mutedColor },
      grid: { horzLines: { color: gridColor } },
    })

    const colorPatch = mainSeriesColorOptions(effectiveChartType, lineColor)
    if (colorPatch) handle.mainSeries.applyOptions(colorPatch)

    // Judged on the vendor's own bars + the live print, never the seeded/extended tape (the
    // seed is dated at this very window's `from`, so it would always agree with it).
    const fixedRange =
      timeframe === '1D'
        ? usableSessionWindow(
            sessionBounds,
            series?.points ?? [],
            liveMatches ? (liveAt ?? null) : null,
          )
        : null
    const isOhlcType = effectiveChartType === 'candle' || effectiveChartType === 'bar'

    // A uniform 1-minute cadence across the ENTIRE fixed session window — real bars slotted
    // into their own minute, every OTHER minute a bare whitespace point (lightweight-charts'
    // own documented mechanism for extending the time axis with no value drawn there) —
    // rather than the real points as-is plus trailing whitespace only. This library spaces
    // bars by INDEX, not elapsed real time, so ANY density mismatch between two stretches of
    // the row distorts their relative on-screen width: the real intraday tape has real GAPS
    // of its own (Yahoo doesn't emit a bar for a minute with no print — confirmed directly:
    // one venue's own "1D" series came back at under half its nominal 1-per-minute density),
    // so real points as-is are already sparser, index-wise, than a trailing fill generated at
    // an exact, gapless 1-per-minute cadence — the real (sparse) portion was compressed into
    // FEWER bar-slots than its actual elapsed time warrants, next to a stretched-out synthetic
    // tail (owner-reproduced live bug, 2026-09-01: a session barely half over by clock time
    // rendered as if ~80% remained). A single uniform grid removes the mismatch: every minute
    // gets exactly one slot, real or blank, so on-screen width tracks real elapsed time
    // regardless of how sparse the vendor's own tape happens to be. Session boundaries
    // (pre/regular/post start & end) are always on-the-minute, so this same grid lands on all
    // of them exactly — no separate boundary-forcing pass needed for the overlay bands below.
    let mainData: Record<string, unknown>[]
    if (fixedRange) {
      mainData = buildFixedRangeGrid(points, fixedRange, isOhlcType)
    } else {
      mainData = isOhlcType
        ? points.map((p) => ({ time: toTime(p.date), ...ohlcOf(p) }))
        : points.map((p) => ({ time: toTime(p.date), value: p.price }))
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    handle.mainSeries.setData(mainData as any)
    handle.mainSeries.applyOptions(mainSeriesLastValueOptions(!!fixedRange))

    if (effectiveChartType === 'baseline') {
      handle.mainSeries.applyOptions({ baseValue: { type: 'price', price: prevClose ?? firstVal } })
    }

    // Real bars (open/close both present) color by that bar's own direction. A degenerate
    // bar (legacy/unavailable OHLC, or the live-tail tick) has open===close===price by
    // construction (ohlcOf's fallback), which would always read "up" — so it falls back to
    // comparing against the prior point's price instead.
    const volumeData = points
      .map((p, i) => {
        if (p.volume == null) return null
        const hasRealOhlc = p.open != null && p.close != null
        const { open, close } = ohlcOf(p)
        const prev = points[i - 1]
        const up = hasRealOhlc ? close >= open : i === 0 || !prev || p.price >= prev.price
        return { time: toTime(p.date), value: p.volume, color: up ? goodColor : badColor }
      })
      .filter((d) => d !== null)
    handle.volumeSeries.setData(volumeData)
    // Soft ceiling so one outlier bar (a closing-auction print routinely dwarfs the rest of
    // the day's per-minute volume) can't drag the whole pane's autoscale out to fit it —
    // see volumeAutoscaleCap's own doc comment. 15% headroom above the cap keeps the
    // tallest NORMAL bars from touching the very top of the pane; the outlier itself simply
    // renders clipped there instead of squashing everything else near zero.
    const volCap = volumeAutoscaleCap(volumeData.map((d) => d.value))
    handle.volumeSeries.applyOptions({
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) =>
        capAutoscaleMax(original(), volCap),
    })

    const showPrevClose = shouldShowPrevCloseLine(showPrevCloseLine, timeframe, prevClose)
    if (showPrevClose) {
      if (handle.priceLine) {
        handle.priceLine.applyOptions({ price: prevClose! })
      } else {
        handle.priceLine = handle.mainSeries.createPriceLine({
          price: prevClose!,
          color: mutedColor,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          // No gutter to draw an axis tag into (rightPriceScale.visible: false) — the custom
          // "Prev close" pill below (updateChartOverlays' prevCloseTag) replaces it.
          axisLabelVisible: false,
          title: 'Prev close',
        })
      }
      // A createPriceLine() annotation is NOT counted in the price axis's own auto-scale by
      // default (lightweight-charts docs: "the chart scales data automatically based on
      // visible data range" — the series' DATA, not an annotation) — so on a big-enough move,
      // prev close sits outside the visible price range and the dashed line renders off-
      // screen above/below the pane (owner-reproduced live bug, 2026-08-31: a Friday close
      // ~7% above the current pre-market price). Widening the range to include it, via the
      // library's own documented autoscaleInfoProvider override, keeps it always visible.
      handle.mainSeries.applyOptions({
        autoscaleInfoProvider: (original: () => AutoscaleInfo | null) =>
          widenAutoscaleToPrice(original(), prevClose),
      })
    } else if (handle.priceLine) {
      handle.mainSeries.removePriceLine(handle.priceLine)
      handle.priceLine = null
      // A real function that passes the default through unmodified, NOT a bare `undefined` —
      // the library's options validator rejected the latter outright ("Invalid option:
      // option", owner-reproduced live crash, 2026-08-31 — this exact line).
      handle.mainSeries.applyOptions({
        autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => original(),
      })
    }

    // Inline read-out: re-fit on every data update (the hardened always-fitted view).
    // Fullscreen: fit on a fresh chart's first paint and on a timeframe switch — never
    // on the 60s poll, so the user's drag/zoom viewport survives live updates.
    if (!fullscreen || fittedForRef.current !== timeframe) {
      // fitContent() alone left a margin on BOTH edges once the dense whitespace tail made
      // this a few-hundred-point series (owner-reproduced live bug, 2026-08-31) — a
      // documented lightweight-charts behavior, not specific to our data: "the library
      // allocates specific width for each data point... series with fewer data points may
      // have a small margin on both sides" (github.com/tradingview/lightweight-charts issue
      // #966/#251). setVisibleLogicalRange pins to BAR INDEXES instead of a time value, which
      // is documented to remove that margin entirely — half-bar ends, so the first and last
      // bar sit ON the pane's edge pixels rather than half a slot in (edgeToEdgeLogicalRange).
      if (fixedRange) {
        handle.chart.timeScale().setVisibleLogicalRange(edgeToEdgeLogicalRange(mainData.length))
      } else {
        handle.chart.timeScale().fitContent()
      }
      fittedForRef.current = timeframe
    }

    // The dotted pre/post-market bands' real boundaries — only while a fixed session window
    // is actually active; null (no bands) otherwise. Read AFTER the range is set above so the
    // very first coordinate lookup reflects it, not a stale pre-fit layout.
    // hasPre/hasPost are EXPLICIT — a delayed-quote venue never gets `pre`/`post` from the
    // backend at all now (no real preMarketPrice/postMarketPrice ever backs them there — see
    // market_status's own with_session_bounds gate), so the absence must mean "don't draw a
    // band", not "fall back to the regular edge and draw one anyway" (owner report
    // 2026-08-31 — the old fallback pushed SOME band regardless, fabricating one for every
    // delayed venue).
    sessionSegmentsRef.current =
      fixedRange && sessionBounds?.regular
        ? {
            preStart: sessionBounds.pre
              ? toTime(sessionBounds.pre.start)
              : toTime(sessionBounds.regular.start),
            regStart: toTime(sessionBounds.regular.start),
            regEnd: toTime(sessionBounds.regular.end),
            postEnd: sessionBounds.post
              ? toTime(sessionBounds.post.end)
              : toTime(sessionBounds.regular.end),
            hasPre: !!sessionBounds.pre,
            hasPost: !!sessionBounds.post,
          }
        : null
    // Same read-back pattern as sessionSegmentsRef just above: updateChartOverlays also runs
    // from a bare resize/range-change callback where this render's own closure variables
    // aren't in scope, so the values it needs for the custom axis labels + "Prev close" pill
    // are relayed through a ref instead.
    axisContextRef.current = {
      prevClose: prevClose ?? null,
      showPrevClose,
      maxFrac: assetClass === 'crypto' ? undefined : 2,
      // Local recompute (the render-scope `seriesCcy` const is declared after this effect in
      // source order — cheaper and clearer to redo the one-liner than depend on closure/TDZ
      // ordering across a deferred effect callback).
      ccy: series?.currency || currency,
    }
    updateChartOverlays()
  }, [
    points,
    timeframe,
    effectiveChartType,
    prevClose,
    showPrevCloseLine,
    containerEl,
    fullscreen,
    sessionBounds,
    themeName,
  ])

  const emptyReason = loading
    ? null
    : chartEmptyReason({
        drawablePoints: points.length,
        timeframe,
        intradayAvailable: series?.intraday_available ?? false,
        marketOpen,
      })

  const seriesCcy = series?.currency || currency
  const marketState = series ? seriesState(series) : null
  // `points` already includes the client-seeded prev-close -> live tail (buildIntradayTape)
  // that draws through a delayed venue's empty open — so a backend "unavailable" state must
  // NOT veto a chart that already has ≥2 drawable points from that seed (owner-reported
  // 2026-09-03: KSTL.DE showed the hard error at the XETRA open despite the header already
  // having both a live quote and a prev close to seed from). The seed itself draws with no
  // visible disclosure (owner ask 2026-09-04: the "Delayed feed" banner read as clutter for
  // the routine first couple of minutes after an open) — a hard "unavailable" error is the
  // only state still surfaced to the user.
  const drawable = points.length >= 2
  const explicitUnavailable = marketState === 'unavailable' && !drawable
  // A previous-session 1D fallback is real, complete data (see `_yf_previous_session_bars`
  // in prices.py) — it must draw the chart, never the hard "unavailable" error, so this is
  // checked BEFORE the generic stale-banner branch below even though the backend also marks
  // it `state: "stale"` (it reuses that bucket rather than widen the 3-value enum for one
  // reason code — see the ADR).
  const previousSession = isPreviousSessionSeries(series)
  // No visual stale/saved-data banner in ANY case (owner ask 2026-09-23, after the
  // 2026-09-21 removal of the "Previous session" one) — the chart just draws what it has;
  // the aria-live announcement below still discloses a stale series to screen readers.
  const announcement = loading
    ? 'Price history is loading.'
    : explicitUnavailable
      ? 'No live or recent saved price history is available for this timeframe.'
      : loadError
        ? 'The price history request failed.'
        : // The "previous session" fallback is fully silent now (owner ask 2026-09-21) — no
          // visual banner and no aria-live disclosure either; the chart just draws.
          previousSession
          ? ''
          : marketState === 'stale' && series
            ? `The chart now shows saved ${series.provider === 'yahoo' ? 'Yahoo' : 'fallback'} price history${series.as_of ? ` from ${relativeUpdatedAt(series.as_of)}` : ''} because live providers are unavailable.`
            : marketState === 'live' && series
              ? `The chart now shows fresh ${series.provider === 'coingecko' ? 'fallback' : series.provider === 'yahoo' ? 'Yahoo' : ''} market data${series.as_of ? ` from ${relativeUpdatedAt(series.as_of)}` : ''}.`
              : emptyReason === 'delayed'
                ? 'The chart is waiting for delayed intraday prices.'
                : emptyReason === 'intraday-unavailable'
                  ? 'Choose a longer timeframe because intraday prices are not stored.'
                  : emptyReason === 'not-enough'
                    ? 'The chart needs more price history before it can draw.'
                    : ''

  const card = (
    <div className="asd-chart-wrap">
      <div className="ui-visually-hidden" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>
      {/* Dotted pre/post-market texture, spanning from the toolbar row's own top down to the
          box's bottom (owner ask 2026-09-21: "put the dots full charts including the top
          controls, like perplexity") — see .asd-chart-future-overlay's own doc comment for
          the stacking-order reasoning. [] when there's no fixed session window. Clipped to the
          card's own rounded corners (owner-reproduced live, 2026-09-21: "why is the dot be
          over the edge?" — a band is a plain rectangle reaching the box's true edge, which
          pokes past the panel's rounded corner without this). Scoped to just the bands, not
          .asd-chart-wrap itself, so overflow: hidden here can't clip the chart-type dropdown
          or hover tooltip that also live in the wrap. */}
      <div className="asd-chart-dots-clip">
        {extendedHourBands.map((band, i) => (
          <div
            key={i}
            className="asd-chart-future-overlay"
            style={
              band.width != null
                ? { left: band.left, width: band.width }
                : { left: band.left, right: 0 }
            }
            aria-hidden="true"
          />
        ))}
      </div>
      <div className="asd-chart-toolbar">
        <div className="asd-chart-tf-cluster">
          {/* Both timeframe controls are always in the DOM; CSS swaps them by the CHART
              COLUMN's own width (container query — the pane narrows when the Lucius panel
              opens at an unchanged viewport): the 8-option segmented control on a wide
              pane, a compact select where it would wrap onto a second line. */}
          <div className="asd-tf-tabs">
            <SegmentedControl
              ariaLabel="Price history timeframe"
              options={TIMEFRAMES}
              value={timeframe}
              onChange={setTimeframe}
            />
          </div>
          <ToolbarMenu
            className="asd-tf-menu"
            ariaLabel="Price history timeframe"
            value={timeframe}
            items={TIMEFRAMES}
            onPick={setTimeframe}
          />
          {/* Moved next to the date-range control (owner ask 2026-09-21) — was in
              .asd-chart-tools on the right, apart from the other view-picking control. */}
          <ChartTypeMenu
            value={effectiveChartType}
            onChange={setChartType}
            ohlcAvailable={ohlcAvailable}
          />
          {/* Moved next to the other controls too (owner ask 2026-09-21) — was in
              .asd-chart-tools on the right, on its own. */}
          {!fullscreen && drawable && (
            <Button
              variant="ghost"
              size="sm"
              aria-label="Expand chart"
              title="Expand chart — drag to pan, scroll to zoom"
              onClick={() => setFullscreen(true)}
            >
              <Maximize2 size={14} />
            </Button>
          )}
        </div>
        <div className="asd-chart-tools">
          {/* Static brand wordmark (owner ask 2026-09-21, "like perplexity") — plain text,
              not a link; .asd-chart-brand hides it under the same 640px breakpoint the
              timeframe controls swap at, so it never crowds the compact dropdown. */}
          <span className="asd-chart-brand" aria-hidden="true">
            Open Net Worth
          </span>
        </div>
      </div>
      {loading ? (
        <div className="asd-chart-box" aria-hidden="true">
          <Loading label="Loading price history…" />
        </div>
      ) : explicitUnavailable ? (
        <div className="asd-chart-box">
          <EmptyState
            title="Price history unavailable"
            body="Live market data is unavailable and there is no recent saved history for this timeframe."
            action={
              <Button variant="secondary" size="sm" onClick={() => setRetryVersion((v) => v + 1)}>
                Try again
              </Button>
            }
          />
        </div>
      ) : loadError ? (
        <div className="asd-chart-box">
          <EmptyState
            title={<span className="ui-state__error-title">Something went wrong</span>}
            body="Could not load price history."
            action={
              <Button variant="secondary" size="sm" onClick={() => setRetryVersion((v) => v + 1)}>
                Retry
              </Button>
            }
          />
        </div>
      ) : emptyReason === 'delayed' ? (
        <div className="asd-chart-box">
          <EmptyState
            title="Live data delayed"
            body="Intraday prices from the exchange are delayed — the chart will fill in shortly."
          />
        </div>
      ) : emptyReason === 'intraday-unavailable' ? (
        <div className="asd-chart-box">
          <EmptyState
            title="Intraday data not available"
            body="1D intraday prices are not stored. Select a longer timeframe to view historical prices."
          />
        </div>
      ) : emptyReason === 'not-enough' ? (
        <div className="asd-chart-box">
          <EmptyState
            title="Not enough price history"
            body="Price history will appear once data is available for this timeframe."
          />
        </div>
      ) : (
        <div className="asd-chart-box" {...DRAG_IGNORE_PROPS}>
          <div ref={setContainerEl} className="asd-chart-lw" />
          {hover && (
            <div
              className="asd-chart-tip"
              style={tipPlacement(hover.x, hover.y, hover.paneW, hover.paneH)}
            >
              <div className="asd-chart-tip-date">{hover.dateLabel}</div>
              <div className="asd-chart-tip-row">
                <span className="asd-chart-tip-k">Open</span>
                <span>
                  {seriesCcy} {fPrice(hover.open, priceDigits(assetClass))}
                </span>
              </div>
              <div className="asd-chart-tip-row">
                <span className="asd-chart-tip-k">High</span>
                <span>
                  {seriesCcy} {fPrice(hover.high, priceDigits(assetClass))}
                </span>
              </div>
              <div className="asd-chart-tip-row">
                <span className="asd-chart-tip-k">Low</span>
                <span>
                  {seriesCcy} {fPrice(hover.low, priceDigits(assetClass))}
                </span>
              </div>
              <div className="asd-chart-tip-row">
                <span className="asd-chart-tip-k">Close</span>
                <span>
                  {seriesCcy} {fPrice(hover.close, priceDigits(assetClass))}
                </span>
              </div>
              {hover.volume != null && (
                <div className="asd-chart-tip-row">
                  <span className="asd-chart-tip-k">Volume</span>
                  <span>{fNum(hover.volume)}</span>
                </div>
              )}
            </div>
          )}
          {/* Custom left-edge price labels — the native right price scale is hidden entirely
              (assetPriceChartLwc's rightPriceScale.visible: false), so nothing else draws
              these; positioned over the canvas (DOM order — after .asd-chart-lw) so they
              stay legible against the series' own area fill. Perplexity-style: numbers inside
              the plot, not in a reserved side gutter. */}
          {axisTicks.map((tick, i) => (
            <span
              key={i}
              className="asd-chart-axis-label"
              style={{ top: tick.y }}
              aria-hidden="true"
            >
              {tick.label}
            </span>
          ))}
          {/* "Prev close" pill — replaces the native price-line axis tag (no gutter left to
              draw it in); same y as the dashed reference line, floating over the right edge. */}
          {prevCloseTag && (
            <div className="asd-chart-prevclose-tag" style={{ top: prevCloseTag.y }}>
              {prevCloseTag.label}
            </div>
          )}
        </div>
      )}
    </div>
  )

  // Fullscreen: the SAME card (same component state — series, timeframe, chart type)
  // rendered inside a workspace Modal instead of inline. The DOM subtree moves, so the
  // chart container remounts and the lifecycle effect rebuilds the chart with pan/zoom
  // enabled; Escape / the header × close it (Modal's own behavior).
  if (fullscreen) {
    return (
      <Modal
        open
        onClose={() => setFullscreen(false)}
        title="Price chart"
        subtitle="Drag to pan • scroll or pinch to zoom"
        size="workspace"
        className="asd-chart-fs-modal"
        overlayClassName="asd-chart-fs-overlay"
        // On a phone this workspace modal docks to the bottom edge as a sheet like every
        // other modal (owner ask 2026-09-21) — the Settings precedent: its own CSS does the
        // docking, this opts it into the shared drag-to-dismiss gesture. The chart pane
        // itself owns its touches (DRAG_IGNORE_PROPS on .asd-chart-box below).
        mobileDockedSheet
      >
        <div className="asd-chart-fs">{card}</div>
      </Modal>
    )
  }
  return card
}
