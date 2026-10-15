// The asset page's value chart, as pure domain math — x/y extents, the hold-forward series, the
// purchase-price line, the year and price ticks, the above/below-cost colour — kept apart from
// the Chart.js component (markChart.tsx) so it's unit-tested without a canvas.
import type { AssetMark, OwnedAsset } from '../../../types/assets'
import { localIso } from '../../../utils/format'

export type ValueColorKey = 'good' | 'bad' | 'neutral'

/** Token NAMES (not `var(...)` refs) — Chart.js draws on canvas, so its chrome needs concrete
 *  colors resolved via `cssToken` at build time, never a CSS variable reference. */
export const COLOR_TOKEN: Record<ValueColorKey, string> = {
  good: '--good',
  bad: '--bad',
  neutral: '--muted',
}

/** Above cost = good, below = bad, no cost basis (or no value yet) = neutral — verbatim. */
export function valueColorKey(value: number | null, cost: number | null): ValueColorKey {
  if (cost == null || value == null) return 'neutral'
  return value >= cost ? 'good' : 'bad'
}

/** "YYYY-MM-DD" → a fractional year (real ISO dates — the prototype's `toYear` parsed a mock
 *  "Mon YYYY" string with a fallback offset; this domain always has a real date). */
export function toYear(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number)
  return (y ?? 0) + ((m ?? 1) - 1) / 12 + ((d ?? 1) - 1) / 365
}

export interface MarkChartSeriesPoint {
  x: number
  y: number
  /** Index into `marksAsc` (oldest → newest); -1 marks the synthetic hold-forward point. */
  i: number
}

export interface MarkChartModel {
  /** Marks oldest → newest, aligned with `series[i].i`. */
  marksAsc: AssetMark[]
  /** The real points plus one synthetic point holding the asset's current value forward to
   *  "today" (`i: -1`) so the step reads as "still this value" — none while it has no value. */
  series: MarkChartSeriesPoint[]
  x0: number
  /** Today (or the newest point, if later) — the axis never runs into a future year. */
  xMax: number
  yMin: number
  yMax: number
  /** Overall line color vs. purchase price (a.value vs a.cost). */
  colorKey: ValueColorKey
  costLine: { x0: number; xMax: number; value: number } | null
  /** Only one point so far — no trend to fill under. */
  single: boolean
}

/** Pure chart domain math — x/y extents, the hold-forward series, the purchase-price line —
 *  split out from the Chart.js-effect component so it's unit-testable without a canvas. */
export function markChartModel(a: OwnedAsset, today: string = localIso()): MarkChartModel {
  const marksAsc = [...a.marks].reverse()
  const points: MarkChartSeriesPoint[] = marksAsc.map((m, i) => ({
    x: toYear(m.date),
    y: m.value,
    i,
  }))
  const first = points[0]!
  const last = points[points.length - 1]!
  // The axis ends today — its right edge is labelled with this year, never a future one.
  const xMax = Math.max(toYear(today), last.x)
  // A lone value (or points bunched at today) still gets a span to draw against.
  const x0 = Math.min(first.x, xMax - (points.length < 2 ? 1 : 0.25))
  // The line holds forward from the asset's current value — `a.value`, never whichever point
  // happens to sort first in `marks`.
  const hold: MarkChartSeriesPoint[] = a.value == null ? [] : [{ x: xMax, y: a.value, i: -1 }]
  // y domain: pad around the points (and the cost line) so one value still gets a range
  const ys = [...points, ...hold].map((p) => p.y).concat(a.cost != null ? [a.cost] : [])
  const yLo = Math.min(...ys)
  const yHi = Math.max(...ys)
  const ySpan = Math.max(yHi - yLo, yHi * 0.2, 1000)
  const yMin = Math.max(0, yLo - ySpan * 0.25)
  const yMax = yHi + ySpan * 0.25
  const series: MarkChartSeriesPoint[] = [...points, ...hold]
  return {
    marksAsc,
    series,
    x0,
    xMax,
    yMin,
    yMax,
    colorKey: valueColorKey(a.value, a.cost),
    costLine: a.cost != null ? { x0, xMax, value: a.cost } : null,
    single: points.length < 2,
  }
}

/** X-axis tick VALUES spanning [x0, xMax], evenly thinned to at most `max` — Chart.js's own
 *  auto-ticker picks "nice" round numbers wherever they fall, which SKIPS the domain's own
 *  start/end year whenever a mark isn't dated exactly on Jan 1st (e.g. an Oct 2024 mark put
 *  x0 at ~2024.75 — a "2024" tick is technically before that, so the chart's real first year
 *  never got a label, reading as if tracking started a year later than it did).
 *
 *  The two edge ticks are the EXACT fractional x0/xMax, not their floor/ceil — a tick VALUE
 *  outside [x0, xMax] risks Chart.js's default `bounds: 'ticks'` stretching the whole
 *  rendered axis to contain it (tried ceil(xMax): a hold-forward point landing at e.g.
 *  2027.02 ceils to 2028, dragging the axis out to a huge empty margin past the real data —
 *  and the opposite direction has the same failure mode: floor(x0) sits BELOW the axis's own
 *  configured min, so its label draws at a negative canvas x and gets clipped, "2024" → "24").
 *  Keeping the edge ticks exactly AT x0/xMax can never expand or misplace anything — their
 *  pixel position is by definition the plot area's own edge. The caller floors each tick's
 *  VALUE only for display (the x-axis `ticks.callback`), never for positioning. */
export function yearTicks(x0: number, xMax: number, max = 6): number[] {
  const firstYear = Math.floor(x0)
  const lastYear = Math.floor(xMax)
  if (lastYear <= firstYear) return [x0]
  const step = Math.max(1, Math.ceil((lastYear - firstYear) / (max - 1)))
  const inner: number[] = []
  for (let y = firstYear + step; y < lastYear; y += step) inner.push(y)
  // Chart.js's `autoSkip` doesn't reliably fire for a manually-injected tick array (it
  // assumes ticks it generated itself) — so a neighbor less than half a year from an edge
  // tick would otherwise render right on top of it (x0 ≈ 2024.75's own label crowding into
  // a "2025" tick barely a quarter-year later). Drop it ourselves instead of depending on
  // Chart.js to notice the overlap. A fixed half-year, not a fraction of the total span —
  // these charts realistically span a few years (assets get re-marked every year or two),
  // where a percentage-based cutoff would either barely trigger or over-trigger depending
  // on how long the asset's been tracked, for the same actual label-width conflict.
  const minGap = 0.5
  if (inner.length > 0 && inner[0]! - x0 < minGap) inner.shift()
  if (inner.length > 0 && xMax - inner[inner.length - 1]! < minGap) inner.pop()
  return [x0, ...inner, xMax]
}

/** Inserts an explicit tick at `pinned` (the asset's purchase price) into an already-
 *  generated ascending Y-axis tick array, dropping any existing tick close enough to crowd
 *  it. Simpler than yearTicks — Chart.js's own "nice number" generator already produces
 *  good Y-axis ticks; this only ADDS the one value someone actually wants to read straight
 *  off the axis (their purchase price) instead of interpolating between generic round
 *  numbers, exactly like the dashed "Purchase price" line has no meaning without it. The
 *  crowding threshold is a fraction of the auto-ticks' OWN average step (not a fixed
 *  number, unlike yearTicks' half-year — a price axis's step varies wildly by asset). */
export function withPinnedTick(ticks: number[], pinned: number): number[] {
  if (ticks.length < 2) return [...ticks, pinned].sort((a, b) => a - b)
  const step = (ticks[ticks.length - 1]! - ticks[0]!) / (ticks.length - 1)
  const minGap = step * 0.4
  const kept = ticks.filter((t) => Math.abs(t - pinned) >= minGap)
  return [...kept, pinned].sort((a, b) => a - b)
}
