import {
  createChart,
  LineSeries,
  AreaSeries,
  BaselineSeries,
  CandlestickSeries,
  BarSeries,
  HistogramSeries,
  CrosshairMode,
  ColorType,
  TickMarkType,
  type AutoscaleInfo,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { fPrice, getDateLocale } from '../../utils/format'
import type { TailPoint } from './priceTail'
import type { SeriesTimeframe } from '../../types/assetDetail'
import type { ChartType } from './ChartTypeMenu'

export interface ChartHandle {
  chart: IChartApi
  container: HTMLDivElement
  chartType: ChartType
  mainSeries: ISeriesApi<SeriesType>
  volumeSeries: ISeriesApi<'Histogram'>
  priceLine: IPriceLine | null
}

export interface ChartTheme {
  goodColor: string
  badColor: string
  mutedColor: string
  gridColor: string
  lineColor: string
  timeVisible: boolean
  fontFamily: string
  /** IANA zone the exchange trades in (e.g. "America/New_York") — the axis/crosshair render
   *  in THIS zone, not the viewer's (see `tickMarkFormatterFor`). Undefined for a venue Yahoo
   *  gives no tz for; falls back to the viewer's local time. */
  timezone?: string
  /** Read live on every axis-tick render, not once at chart creation — see
   *  `tickMarkFormatterFor`'s own docs for why a plain boolean can't do this. */
  isIntraday?: () => boolean
  /** Price/time-scale label font size, px. Library default is 12 — a narrow (mobile)
   *  container passes something smaller, shrinking the price-scale's own width (it's
   *  sized to fit its widest rendered label) without dropping any digits — a quarter of a
   *  316px-wide mobile chart card was the axis gutter alone (owner-reproduced live
   *  measurement, 2026-09-01: 76px of 316px, "right padding out of nowhere"). Undefined
   *  keeps the library default. */
  fontSize?: number
  /** Caps the axis labels AND the last-value/crosshair price tag at this many decimals —
   *  the SAME `priceFormat.formatter` renders both, so this can't target just one. Same
   *  rule as the price header (AssetDetailView's `priceMaxFrac`): 2 for an equity, left
   *  undefined for crypto (keeps real sub-cent precision). Complements `fontSize` above —
   *  together they're what actually freed the gutter width, not fontSize alone. */
  priceMaxFrac?: number
}

/** ISO date string -> UNIX-seconds timestamp. lightweight-charts treats a plain
 *  'yyyy-mm-dd' string as a whole business day with no time component, which would
 *  collapse an intraday (1D/5D) tape onto one bucket per day — UTCTimestamp (seconds)
 *  is required for real intraday granularity. */
export function toTime(date: string): UTCTimestamp {
  return Math.floor(new Date(date).getTime() / 1000) as UTCTimestamp
}

/** Whether `locale`'s DEFAULT hour cycle is 12-hour — the documented way to ask Intl this
 *  (`resolvedOptions().hour12` after requesting an hour field), rather than inferring it
 *  from whether a `dayPeriod` part happens to come back non-empty. */
function localeIs12Hour(locale: string | undefined): boolean {
  try {
    return new Intl.DateTimeFormat(locale, { hour: 'numeric' }).resolvedOptions().hour12 ?? false
  } catch {
    return false
  }
}

/** Extracts the calendar/clock fields for `time` AS SEEN IN `timezone` (an IANA name, e.g.
 *  "America/New_York") — `Intl.DateTimeFormat.formatToParts` with explicit component fields
 *  (never `dateStyle`/`timeStyle`, which throw when combined with other options — the exact
 *  production crash this codebase already hit once, see AssetDetailView's price caption).
 *  `timezone` undefined falls back to the JS runtime's own default (the browser's local
 *  zone) via `Intl`'s own documented behavior for an omitted `timeZone` option.
 *
 *  Locale comes from `getDateLocale()` (the user's own date_format preference, same source
 *  `fDate`/`fTime` read) rather than a fixed `'en-US'`, so this follows the user's own
 *  AM/PM-vs-24h setting instead of a hardcoded convention. `hour12` is passed explicitly
 *  (computed once via `localeIs12Hour`, not left for Intl to infer per call) — `dayPeriod`
 *  is deliberately NEVER requested as its own option: asking for it explicitly (even just
 *  to force simple "AM"/"PM") makes Intl return CLDR's "flexible day periods" instead
 *  ("in the morning", "in the evening" — a relative-time feature, not a simple marker),
 *  verified directly against the engine (owner-reproduced live bug, 2026-09-01, this exact
 *  line). Plain "AM"/"PM" comes back on its own, IMPLICITLY, as part of `hour12: true`
 *  formatting — simply never ask for `dayPeriod` and it's correct. */
function partsInZone(time: Time, timezone: string | undefined) {
  const date = new Date((time as number) * 1000)
  const locale = getDateLocale()
  const hour12 = localeIs12Hour(locale)
  const parts = new Intl.DateTimeFormat(locale, {
    timeZone: timezone,
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12,
  }).formatToParts(date)
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  // Intl's 24-hour formatting still emits "24" for local midnight in some locales/engines —
  // normalize that one edge case (moot for a 12h locale, where midnight reads "12").
  const hour = get('hour') === '24' ? '00' : get('hour')
  return {
    year: get('year'),
    month: get('month'),
    day: get('day'),
    hour,
    minute: get('minute'),
    second: get('second'),
    // Present only when the locale rendered 12-hour ("AM"/"PM" or a locale's own
    // equivalent) — absent for a 24h locale.
    dayPeriod: get('dayPeriod'),
  }
}

/** "14:32" (24h locale) or "2:32 PM" (12h locale) — the SAME hour/minute fields
 *  `partsInZone` already extracted, just assembled with or without the AM/PM suffix. */
function timeLabel(p: ReturnType<typeof partsInZone>): string {
  return p.dayPeriod ? `${p.hour}:${p.minute} ${p.dayPeriod}` : `${p.hour}:${p.minute}`
}

/** lightweight-charts formats its OWN built-in axis tick marks and crosshair time label
 *  using UTC internally (verified against the bundled library — it exclusively calls
 *  `getUTC*` Date methods, never local ones), regardless of the browser's timezone. A 1D/5D
 *  intraday chart is fundamentally about a SPECIFIC exchange's trading session (the fixed
 *  session window is sourced from that exchange's own calendar — see `sessionWindowRange`),
 *  so the axis renders in the EXCHANGE's timezone, not the viewer's — otherwise a viewer far
 *  from the exchange sees the fixed session window's hours wrap past their own local midnight
 *  into a confusing next-day/next-month tick (owner-reproduced live bug, 2026-08-31: a NASDAQ
 *  4am-8pm EDT session, viewed from a UTC+8 phone, rendered as "23:00 ... 02:00 ... Sep '26"
 *  despite the whole session being on the SAME exchange-local calendar day). Longer
 *  timeframes (1M+) have no intraday hours to render and are date-only, so the zone only
 *  matters here. Hand-formatted (not `toLocaleDateString`) to reproduce the library's own
 *  "dd MMM 'yy" default style deterministically, rather than an ambiguous locale-dependent
 *  month/day ordering. `timezone` undefined (a venue Yahoo gives no tz for) falls back to the
 *  viewer's own local time — the previous, pre-existing behavior for that case.
 *
 *  `isIntraday` is read on EVERY call, not once at formatter-creation time — the chart
 *  instance isn't recreated on a plain timeframe switch (the SegmentedControl), so a
 *  closure-captured boolean would go stale the moment the user picked a different
 *  timeframe without a fresh mount. When it reports 1D, EVERY tick renders as a plain
 *  time regardless of what `tickMarkType` the library assigned that tick: the library
 *  decides Year/Month/DayOfMonth promotion using its OWN internal UTC calendar math,
 *  unaware of the exchange timezone this formatter renders in — a 1D session's fixed
 *  window is by construction a SINGLE exchange-local calendar day, but its last bar
 *  (a US-timezone post-market close) can land exactly on a UTC month/day boundary,
 *  which the library reads as "this tick deserves a Month/Year label" even though
 *  nothing calendar-significant happened there in the exchange's own timezone
 *  (owner-reproduced live bug, 2026-08-31: a lone "Aug '26" tick at the far right edge
 *  of a chart that never left Aug 31 in EDT — the only non-time label in the whole
 *  row, on a window that both starts and ends within the same exchange-local day).
 *  5D+ still wants real day-boundary labels (it genuinely spans multiple days), so this
 *  only overrides for 1D. */
// Whitespace wrapped around EVERY time-axis label, both sides — the "padding only for the
// time text, not the whole chart" the owner asked for (2026-09-21) without touching the plot:
// lightweight-charts clamps an overflowing edge label to exactly [0, width] using the string's
// MEASURED width (`_alignTickMarkLabelCoordinate`) and never trims it, and it picks tick
// density from a character-count estimate, not the strings — so the pad moves only the labels
// that were touching the border (they land inset by the pad) while centred labels keep their
// centre and the tick set is unchanged. An en space + a normal space ≈ 8px (--space-3) at the
// 11px axis font. Leans on two long-stable library behaviours; if either changed, the labels
// would merely touch the edge again.
export const AXIS_LABEL_EDGE_PAD = '\u2002 '

export function tickMarkFormatterFor(timezone: string | undefined, isIntraday?: () => boolean) {
  return (time: Time, tickMarkType: TickMarkType): string =>
    `${AXIS_LABEL_EDGE_PAD}${rawTickLabel(time, timezone, isIntraday?.() ? undefined : tickMarkType)}${AXIS_LABEL_EDGE_PAD}`
}

function rawTickLabel(time: Time, timezone: string | undefined, tickMarkType?: TickMarkType) {
  const p = partsInZone(time, timezone)
  switch (tickMarkType) {
    case TickMarkType.Year:
      return p.year
    case TickMarkType.Month:
      return `${p.month} '${p.year.slice(-2)}`
    case TickMarkType.DayOfMonth:
      return p.day
    case TickMarkType.TimeWithSeconds:
      return p.dayPeriod
        ? `${p.hour}:${p.minute}:${p.second} ${p.dayPeriod}`
        : `${p.hour}:${p.minute}:${p.second}`
    default:
      return timeLabel(p)
  }
}

export function crosshairTimeFormatterFor(timezone: string | undefined) {
  return (time: Time): string => {
    const p = partsInZone(time, timezone)
    return `${parseInt(p.day, 10)} ${p.month} '${p.year.slice(-2)}  ${timeLabel(p)}`
  }
}

/** A point's OHLC, degenerating to its `price` (close alias) when the backend didn't
 *  send full OHLC for that bar — legacy data, `ohlc_available: false`, or the
 *  appended live-tail tick (a single quote, not a real bar). */
export function ohlcOf(p: TailPoint): { open: number; high: number; low: number; close: number } {
  return {
    open: p.open ?? p.price,
    high: p.high ?? p.price,
    low: p.low ?? p.price,
    close: p.close ?? p.price,
  }
}

/** #rgb / #rrggbb -> rgba(...). lightweight-charts' color parser accepts hex/rgb/hsl/
 *  hwb/named/transparent — NOT CSS color-mix() — so translucent fills are built by
 *  hand instead of the color-mix() trick the Chart.js version leaned on (which worked
 *  there only because a native canvas 2D context resolves color-mix() itself). Every
 *  design token this reads (--good/--bad/etc.) is stored as hex, so this is exact. */
export function hexAlpha(hex: string, alpha: number): string {
  const h = hex.replace('#', '')
  const full =
    h.length === 3
      ? h
          .split('')
          .map((c) => c + c)
          .join('')
      : h
  const n = parseInt(full, 16)
  const r = (n >> 16) & 255
  const g = (n >> 8) & 255
  const b = n & 255
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/** Cursor-following OHLCV-tooltip placement: anchored at the crosshair point, offset
 *  12px toward the pane's center, flipping to the other side of the cursor past the
 *  pane midpoint — so the tip never clips at an edge, with no DOM measurement needed
 *  (the flip is a translate(-100%), sized by the tip itself). Pure for testability. */
export function tipPlacement(
  x: number,
  y: number,
  paneWidth: number,
  paneHeight: number,
): { left: number; top: number; transform: string } {
  const dx = x > paneWidth / 2 ? 'calc(-100% - 12px)' : '12px'
  const dy = y > paneHeight / 2 ? 'calc(-100% - 12px)' : '12px'
  return { left: x, top: y, transform: `translate(${dx}, ${dy})` }
}

/** The value the line/mountain color compares the LAST price against. On 1D that must be
 *  prev close — the same comparator the detail header's delta uses — or the two disagree
 *  (an observed issue: ONWAI.DE read -0.08% red in the header while the 1D line
 *  painted green, because the line compared against the day's first plotted point).
 *  Longer timeframes keep the first-point baseline: prev close only describes today. */
export function seriesBaseline(
  timeframe: SeriesTimeframe,
  firstVal: number,
  prevClose: number | null | undefined,
): number {
  if (timeframe === '1D' && prevClose != null && Number.isFinite(prevClose)) return prevClose
  return firstVal
}

/** Whether to draw the dashed "Prev close" reference line + axis label. Gated on the
 *  caller's showPrevCloseLine (false for a 24/7 asset with no closing bell — crypto, owner
 *  call 2026-08-11), the timeframe (only 1D/5D are recent enough for it to be meaningful),
 *  and having a real, finite value to draw. Deliberately separate from seriesBaseline above:
 *  that comparator must keep using the real prevClose regardless (so the line color still
 *  agrees with the header's % change) — only this VISIBLE annotation is asset-type-specific. */
export function shouldShowPrevCloseLine(
  showPrevCloseLine: boolean,
  timeframe: SeriesTimeframe,
  prevClose: number | null | undefined,
): boolean {
  return (
    showPrevCloseLine &&
    (timeframe === '1D' || timeframe === '5D') &&
    prevClose != null &&
    Number.isFinite(prevClose)
  )
}

/** The 1D chart's fixed visible range spanning the FULL trading day — real pre/regular/post
 *  bounds from Yahoo's own currentTradingPeriod (`MarketStatus.session_bounds`), never a
 *  hardcoded local-hour guess (owner ask 2026-08-31). `from` is the earliest of pre/regular
 *  start present, `to` the latest of regular/post end present, so a venue missing pre OR post
 *  data still gets a sane (if narrower) fixed window instead of none. Null when no bound at
 *  all is present — caller falls back to fitContent(). */
export function sessionWindowRange(
  bounds:
    | {
        pre?: { start: string; end: string }
        regular?: { start: string; end: string }
        post?: { start: string; end: string }
      }
    | null
    | undefined,
): { from: UTCTimestamp; to: UTCTimestamp } | null {
  if (!bounds) return null
  const starts = [bounds.pre?.start, bounds.regular?.start]
    .filter((s): s is string => !!s)
    .map((s) => new Date(s).getTime())
    .filter(Number.isFinite)
  const ends = [bounds.regular?.end, bounds.post?.end]
    .filter((s): s is string => !!s)
    .map((s) => new Date(s).getTime())
    .filter(Number.isFinite)
  if (!starts.length || !ends.length) return null
  return {
    from: Math.floor(Math.min(...starts) / 1000) as UTCTimestamp,
    to: Math.floor(Math.max(...ends) / 1000) as UTCTimestamp,
  }
}

// A fixed session window is only usable when the tape it is about to grid belongs to it.
// For the minutes after a pre-market open the cached chart meta can still describe the
// PREVIOUS session while the 1-minute tape and the live print are already today's (the
// backend now expires that cache at the roll, `prices._next_session_has_begun`, but a poll
// can still straddle it): gridding Monday's first print onto Friday's window drew 66 hours of
// whitespace with the live dot at the far right — "pre-market on the right side instead of
// left" (owner-reproduced live, 2026-09-21, MSFT 04:02 ET). A tape more than this far past
// the window's end cannot be that session's; the ordinary overshoot is the live print a few
// seconds past post.end.
const SESSION_WINDOW_SLACK_SECONDS = 3600

/** `sessionWindowRange(bounds)`, or null when the tape (its newest bar or the live print)
 *  has run more than `SESSION_WINDOW_SLACK_SECONDS` past that window — stale bounds from the
 *  previous session; the caller then falls back to the plain fit until they catch up. */
export function usableSessionWindow(
  bounds: Parameters<typeof sessionWindowRange>[0],
  points: TailPoint[],
  liveAt: string | null,
): { from: UTCTimestamp; to: UTCTimestamp } | null {
  const range = sessionWindowRange(bounds)
  if (!range) return null
  const stamps = [points[points.length - 1]?.date, liveAt]
    .filter((d): d is string => !!d)
    .map((d) => new Date(d).getTime() / 1000)
    .filter(Number.isFinite)
  if (!stamps.length) return range
  return Math.max(...stamps) > range.to + SESSION_WINDOW_SLACK_SECONDS ? null : range
}

/** Slots `points` onto a UNIFORM 1-minute grid spanning `range` — every minute gets exactly
 *  one bar-slot, real or blank (lightweight-charts' own whitespace mechanism), so on-screen
 *  width tracks real elapsed time regardless of how sparse the vendor's own tape is (see
 *  the caller's own comment on why a uniform grid matters). Each point is bucketed into the
 *  MINUTE it falls in, not its own exact second — a real bar (or the appended live tail)
 *  almost never lands on an exact :00 (owner-reproduced 2026-09-01: a bar timestamped 5
 *  seconds past the minute was silently dropped when the grid only matched EXACT multiples
 *  of 60s from `range.from`, leaving the chart's own last-plotted value stuck on an older
 *  on-the-minute bar while the detail header — reading the live quote directly, ungridded —
 *  had already moved on, visibly disagreeing with it). `Map` keeps the LAST point written
 *  per bucket, so two points sharing a minute (a real bar plus a same-minute live tail)
 *  still resolve to the newer one — "whatever's newer wins", same principle as the header's
 *  own reconciliation against the chart. Extends past `range.to` when the tape's own last
 *  point is later (mid-session, "now" is still inside the window). The grid's LAST slot is
 *  the session's last MINUTE (`range.to - 60`), not its end instant: `to` is Yahoo's
 *  post.end (20:00:00) and a minute bar is stamped at its START, so the newest bar a
 *  completed session can hold is 19:59 — a slot AT 20:00 is whitespace forever, and the
 *  line ended one bar short of the pane's edge on every finished day (owner-reproduced
 *  live, 2026-09-21). A point bucketed at or past `to` still gets its slot. Pure for
 *  testability. */
export function buildFixedRangeGrid(
  points: TailPoint[],
  range: { from: UTCTimestamp; to: UTCTimestamp },
  isOhlcType: boolean,
): Record<string, unknown>[] {
  const STEP_SECONDS = 60
  const byTime = new Map(
    points.map((p) => [
      range.from + Math.floor((toTime(p.date) - range.from) / STEP_SECONDS) * STEP_SECONDS,
      p,
    ]),
  )
  const lastPointTime = points.length ? toTime(points[points.length - 1]!.date) : range.from
  const lastBucket =
    range.from + Math.floor((lastPointTime - range.from) / STEP_SECONDS) * STEP_SECONDS
  const to = Math.max(range.to - STEP_SECONDS, lastBucket)
  const built: Record<string, unknown>[] = []
  for (let t: number = range.from; t <= to; t += STEP_SECONDS) {
    const p = byTime.get(t)
    built.push(p ? { time: t, ...(isOhlcType ? ohlcOf(p) : { value: p.price }) } : { time: t })
  }
  return built
}

// The two `autoscaleInfoProvider` bodies. lightweight-charts' `original()` returns
// `{ priceRange: null }` — NOT null — when the visible range holds no valued bar (its own
// AutoscaleInfo typing), which an all-whitespace 1D grid produces: the first poll after a
// pre-market open, before the first print lands (owner-reproduced live crash, 2026-09-21,
// MSFT 04:01 ET: "Cannot read properties of null (reading 'minValue')" took the whole Intel
// tab down to the error boundary). Both pass such a result through untouched.

/** Widens the auto-scaled price range to include `price` (the prev-close line, which the
 *  library's autoscale otherwise ignores — a big gap move pushed it off the pane). */
export function widenAutoscaleToPrice(
  res: AutoscaleInfo | null,
  price: number | null | undefined,
): AutoscaleInfo | null {
  if (!res?.priceRange || price == null || !Number.isFinite(price)) return res
  res.priceRange.minValue = Math.min(res.priceRange.minValue, price)
  res.priceRange.maxValue = Math.max(res.priceRange.maxValue, price)
  return res
}

/** Caps the auto-scaled range's top at `cap` plus 15% headroom (the volume pane: a single
 *  closing-auction spike otherwise flattens every other bar — see `volumeAutoscaleCap`). */
export function capAutoscaleMax(
  res: AutoscaleInfo | null,
  cap: number | null,
): AutoscaleInfo | null {
  if (!res?.priceRange || cap == null) return res
  res.priceRange.maxValue = Math.min(res.priceRange.maxValue, cap * 1.15)
  return res
}

/** Per-type applyOptions patch that recolors the main series for the day's direction on
 *  each data update. Line/mountain recolor the whole series (their last-value marker
 *  follows the series color). Candle/bar keep per-bar direction colors but pin
 *  `priceLineColor`: the last-value marker (dotted line + axis tag) otherwise defaults to
 *  the LAST bar's color, and the appended live tick is a flat bar (open === close) that
 *  always reads "up" — a green tag under a red header (an observed issue, reproduced on ONWAI.DE).
 *  Baseline needs nothing: its colors are prev-close-relative by construction. */
export function mainSeriesColorOptions(
  type: ChartType,
  lineColor: string,
): Record<string, unknown> | null {
  if (type === 'line') return { color: lineColor }
  if (type === 'mountain') {
    return {
      lineColor,
      topColor: hexAlpha(lineColor, 0.28),
      bottomColor: hexAlpha(lineColor, 0.02),
    }
  }
  if (type === 'candle' || type === 'bar') return { priceLineColor: lineColor }
  return null
}

/** The main series' built-in "last value" line/label (lightweight-charts default: ON) draws a
 *  dashed line from the last REAL point across to the right price axis — sensible on a normal,
 *  densely-populated chart where the last point already sits near "now" (the right edge). On the
 *  1D fixed session grid, though, "now" can be minutes into an 8+ hour pane padded with blank
 *  whitespace out to market close: the dashed line then stretches across the entire not-yet-
 *  happened rest of the day, and — whenever price hasn't moved yet — lands exactly on the
 *  always-full-width "Prev close" reference line, merging into what reads as ONE flat line
 *  spanning the whole chart (owner-reported 2026-09-04: KSTL.DE two minutes into the XETRA open
 *  looked like a fully-flat day instead of a barely-started one). Off whenever the fixed grid is
 *  active — the header's live price and the "Prev close" line already carry that context. */
export function mainSeriesLastValueOptions(hasFixedRange: boolean): {
  lastValueVisible: boolean
  priceLineVisible: boolean
} {
  const visible = !hasFixedRange
  return { lastValueVisible: visible, priceLineVisible: visible }
}

/** The logical range that fits `count` bars EDGE TO EDGE. lightweight-charts centres every
 *  bar in its own slot and pins the scale one pixel in from the right (`indexToCoordinate`:
 *  `x = width - (deltaFromRight + 0.5) * barSpacing - 1`), so the obvious `{from: 0, to: N-1}`
 *  lands the last bar at `width - barSpacing/2 - 1` — a slot's half plus a pixel of nothing
 *  between the line's end and the pane's edge (owner-reproduced live, 2026-09-21: "a 1px empty
 *  space between the line and the edge"; the post-market band already worked around the same
 *  offset with `right: 0`). Half-bar ends shrink the count by one slot (barSpacing =
 *  width/(N-1)) and set rightOffset to -0.5, which puts bar N-1 on the LAST pixel column and
 *  bar 0 on the first. The lock-on-resize keeps the offset, so a re-layout stays flush. */
export function edgeToEdgeLogicalRange(count: number): { from: number; to: number } {
  if (count < 2) return { from: 0, to: 0 }
  return { from: 0.5, to: count - 1.5 }
}

/** Axis price label + the last-value/crosshair price tag (the SAME `priceFormat.formatter`
 *  renders both — no way to target just one): compact 'k' for big numbers, fPrice (sub-cent
 *  aware) otherwise. `maxFractionDigits` caps an equity to 2dp, same rule and same reason as
 *  the price header (AssetDetailView's `priceMaxFrac`) — left undefined for crypto, which
 *  keeps real sub-cent precision. Without it, a stock's price tag showed up to 4 decimals
 *  ("507.6606") — needless precision AND extra width in the same crowded gutter `fontSize`
 *  below is fighting for (owner-reproduced live measurement, 2026-09-01). */
export function formatAxisPrice(n: number, maxFractionDigits?: number): string {
  if (Math.abs(n) >= 1000) return (n / 1000).toFixed(1) + 'k'
  if (n === 0) return '0'
  return fPrice(n, maxFractionDigits)
}

/** "Nice" round tick values spanning `[min, max]` — the standard axis algorithm (a step of
 *  1/2/5 × 10^k closest to the range divided by `targetCount`), so a custom-drawn axis lines
 *  up with values a reader actually expects (70.2, 70.4, 70.6…) instead of raw fractions of
 *  the pixel range. Used for the chart's own left-edge price labels (owner ask 2026-09-21:
 *  "like Perplexity" — full-bleed chart, numbers drawn INSIDE the plot instead of in a
 *  reserved right-side gutter) — the native price scale is hidden (`buildChartOptions`'s
 *  `rightPriceScale.visible: false`), so there is no library-computed tick set to read back;
 *  this re-derives one from the same visible-range API every caller already has. Returns []
 *  for a degenerate/inverted range or a non-positive `targetCount`. */
// The most price labels the 300px pane ever shows. The closest-nice-step rule below allows
// up to ~8 for an unlucky range (a 1.5-point pre-market range at target 5 picked step 0.2:
// nine labels, one every ~30px, drawn over the line — owner-reproduced 2026-09-21, MSFT:
// "how to make this less busy"); past this many the step moves up to the next nice one.
export const MAX_AXIS_TICKS = 6

export function niceTicks(min: number, max: number, targetCount = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min || targetCount < 1) return []
  const rawStep = (max - min) / targetCount
  const magnitude = 10 ** Math.floor(Math.log10(rawStep))
  const residual = rawStep / magnitude
  // Geometric-mean thresholds between consecutive nice numbers (√2≈1.41, √10≈3.16, √50≈7.07)
  // — the standard "closest nice step" boundary, not a linear one: residual 2.12 (a 70.19…71.25
  // range at targetCount 5) must land on 2 (step 0.2), not 5 (step 0.5, too coarse).
  const niceResidual =
    residual < Math.sqrt(2) ? 1 : residual < Math.sqrt(10) ? 2 : residual < Math.sqrt(50) ? 5 : 10
  const ticks = ticksAtStep(min, max, niceResidual * magnitude)
  if (ticks.length <= MAX_AXIS_TICKS) return ticks
  const coarser = { 1: 2, 2: 5, 5: 10, 10: 20 }[niceResidual]! * magnitude
  return ticksAtStep(min, max, coarser)
}

function ticksAtStep(min: number, max: number, step: number): number[] {
  const start = Math.ceil(min / step) * step
  const ticks: number[] = []
  // `<= max + step / 2`, not `<= max`: a tick that lands exactly on (or a hair under, from
  // float error) the visible range's own top edge must still be included.
  for (let v = start; v <= max + step / 2; v += step) {
    ticks.push(Math.round(v / step) * step) // snap away from float drift (70.19999999999999)
  }
  return ticks
}

/** A soft autoscale ceiling for the volume pane — the 97th percentile of the visible bars,
 *  not the true max. A single outlier bar (e.g. an exchange's whole closing-auction print
 *  landing on one minute — owner-reproduced live, 2026-09-21: "the spike make the other
 *  columns too small, impossible to read") otherwise drags the pane's autoscale out to fit
 *  IT, squashing every normal bar down near zero height. Capping lets that one bar exceed
 *  the visible pane (drawn clipped at the top — a standard, accepted convention for volume
 *  spikes) while the rest of the day scales to something actually readable. Returns null
 *  below 3 positive values — not enough of a distribution to single out an outlier from. */
export function volumeAutoscaleCap(values: number[]): number | null {
  const positive = values.filter((v) => Number.isFinite(v) && v > 0).sort((a, b) => a - b)
  if (positive.length < 3) return null
  const idx = Math.min(Math.floor(positive.length * 0.97), positive.length - 1)
  return positive[idx] ?? null
}

function buildPriceFormat(maxFractionDigits?: number) {
  return {
    type: 'custom' as const,
    formatter: (n: number) => formatAxisPrice(n, maxFractionDigits),
    minMove: 0.0001,
  }
}

function addMainSeries(
  chart: IChartApi,
  type: ChartType,
  theme: ChartTheme,
): ISeriesApi<SeriesType> {
  const { goodColor: good, badColor: bad, lineColor } = theme
  const priceFormat = buildPriceFormat(theme.priceMaxFrac)
  if (type === 'candle') {
    return chart.addSeries(
      CandlestickSeries,
      {
        upColor: good,
        downColor: bad,
        wickUpColor: good,
        wickDownColor: bad,
        borderVisible: false,
        priceFormat,
      },
      0,
    )
  }
  if (type === 'bar') {
    return chart.addSeries(BarSeries, { upColor: good, downColor: bad, priceFormat }, 0)
  }
  if (type === 'baseline') {
    return chart.addSeries(
      BaselineSeries,
      {
        baseValue: { type: 'price', price: 0 }, // set for real per-render via applyOptions (prior close)
        topLineColor: good,
        bottomLineColor: bad,
        topFillColor1: hexAlpha(good, 0.28),
        topFillColor2: hexAlpha(good, 0.05),
        bottomFillColor1: hexAlpha(bad, 0.05),
        bottomFillColor2: hexAlpha(bad, 0.28),
        lineWidth: 2,
        priceFormat,
      },
      0,
    )
  }
  if (type === 'mountain') {
    return chart.addSeries(
      AreaSeries,
      {
        lineColor,
        topColor: hexAlpha(lineColor, 0.28),
        bottomColor: hexAlpha(lineColor, 0.02),
        lineWidth: 2,
        priceFormat,
      },
      0,
    )
  }
  return chart.addSeries(LineSeries, { color: lineColor, lineWidth: 2, priceFormat }, 0)
}

function addVolumeSeries(chart: IChartApi): ISeriesApi<'Histogram'> {
  const series = chart.addSeries(
    HistogramSeries,
    {
      priceFormat: { type: 'volume' },
      color: '#888888',
      // No last-value pill / dashed baseline for volume: the latest bar is the
      // in-progress interval, whose volume reads ~0 on a delayed feed — an axis
      // label stuck at "0" reads as a bug, and per-bar volume is already in the
      // hover tooltip. (The price series keeps its own last-value label.)
      lastValueVisible: false,
      priceLineVisible: false,
    },
    1,
  )
  // 6:1 stretch ratio (was 3:1, owner ask 2026-08-31: "volume one is too high") — volume
  // reads as a thin strip under the price pane, not a peer taking a third of the box.
  chart.panes()[0]?.setStretchFactor(6)
  chart.panes()[1]?.setStretchFactor(1)
  return series
}

/** Chart options as a pure function so the interaction wiring is unit-testable.
 *  `interactive: false` is the inline fitted read-out (crosshair hover only, matching
 *  the old Chart.js chart); `interactive: true` is the fullscreen mode — the library's
 *  native drag-pan + wheel/pinch zoom are enabled and the viewport belongs to the user
 *  (the caller must then also stop re-fitting on every data update). */
export function buildChartOptions(theme: ChartTheme, interactive: boolean) {
  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: theme.mutedColor,
      fontFamily: theme.fontFamily,
      ...(theme.fontSize != null ? { fontSize: theme.fontSize } : {}),
      // The on-chart TradingView logo is optional under the license as long as the
      // written attribution notice + link is shown elsewhere (Settings → Data → Credits).
      attributionLogo: false,
      // The library's own default here is a dark, always-visible divider between the price
      // and volume panes WITH a drag-to-resize handle — nobody asked for a manually
      // resizable split, and the line itself read as an unexplained extra divider next to
      // the dotted texture and the box's own bottom edge (owner-reproduced live, 2026-09-21,
      // "why do we have 3 horizontal lines here"). Matches the price/volume ratio staying
      // fixed at the 6:1 stretch factor set below regardless.
      panes: { separatorColor: 'transparent', enableResize: false },
    },
    grid: { vertLines: { visible: false }, horzLines: { color: theme.gridColor } },
    // No reserved axis gutter (owner ask 2026-09-21, "like Perplexity"): the plot — and the
    // dotted pre/post-market texture painted behind it — runs to the container's true right
    // edge. `visible: false` still leaves the scale doing its normal value<->coordinate math
    // (gridlines keep drawing at the SAME positions; `priceToCoordinate` keeps working) — it
    // only stops the library from reserving screen space for its own label column, which is
    // what AssetPriceChart's own custom left-edge tick labels + "Prev close" pill replace.
    rightPriceScale: { visible: false, borderVisible: false },
    timeScale: {
      borderVisible: false,
      timeVisible: theme.timeVisible,
      secondsVisible: false,
      tickMarkFormatter: tickMarkFormatterFor(theme.timezone, theme.isIntraday),
      // lightweight-charts' DEFAULT resize behavior is a trader's chart: keep bar
      // spacing + the right edge anchored and let the VISIBLE RANGE change — so
      // shrinking the container silently dropped the earliest bars off the left
      // ("shows 14:15, not 14:00") and growing it left dead space instead of
      // stretching the day to fill. Locking preserves the visible range across
      // resizes in BOTH modes: the fitted full-range view inline, the user's own
      // pan/zoom viewport in fullscreen.
      lockVisibleTimeRangeOnResize: true,
      // The 1D fixed session window (see sessionWindowRange) fills the trailing gap
      // with a real whitespace bar every 60s out to the session's own close/post-close
      // (see the data-update effect) — a full pre+regular+post day is ~950 one-minute
      // bars. The library's DEFAULT minBarSpacing (0.5px) refuses to compress a
      // requested full-range fit below that floor, so on a narrow (mobile-width) chart
      // box — under ~475px for ~950 bars — it silently clamps the visible range short
      // of what setVisibleLogicalRange({from: 0, ...}) asked for, leaving the pre-market
      // boundary outside the actually-rendered range entirely. The pre/post-market
      // overlay bands below are positioned from that SAME boundary via timeToCoordinate,
      // so a clamped range put them off-canvas (owner-reproduced live bug, 2026-08-31:
      // "dots outside of the chart area, on the left side" on a phone-width viewport).
      // A near-zero floor lets a mobile-width fit compress that many bars into however
      // few pixels are available instead of refusing to.
      minBarSpacing: 0.01,
    },
    crosshair: { mode: CrosshairMode.Normal },
    localization: { timeFormatter: crosshairTimeFormatterFor(theme.timezone) },
    handleScroll: interactive,
    handleScale: interactive,
  }
}

/** Build a fresh chart + main series + volume pane. Called only when the container,
 *  the effective chart type, or the interaction mode changes — plain data refreshes
 *  (the 60s intraday poll) reuse the same instance via `setData` so the chart never
 *  flickers/rebuilds. */
export function buildChart(
  container: HTMLDivElement,
  type: ChartType,
  theme: ChartTheme,
  interactive = false,
): ChartHandle {
  const chart = createChart(container, {
    ...buildChartOptions(theme, interactive),
  })
  const mainSeries = addMainSeries(chart, type, theme)
  const volumeSeries = addVolumeSeries(chart)
  return { chart, container, chartType: type, mainSeries, volumeSeries, priceLine: null }
}
