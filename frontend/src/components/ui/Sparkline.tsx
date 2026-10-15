import { useId } from 'react'
import { gapRuns, type GapRun } from './sparklineGap'

// Sparkline — a small area+line SVG chart (the Investments masthead's 30D line, the Intel pulse
// rows' trend). Optional `compare` draws a second series (a benchmark) as a faint dashed line on
// the SAME scale; optional `band` shades a horizontal price range (your buy zone), clipped to it.
// `gapFill` shades the space BETWEEN the two lines instead of under the main one — green where
// the main line is ahead, red where it is behind — so the gap reads without a caption.

interface Props {
  values: number[]
  up: boolean
  width?: number
  height?: number
  compare?: number[] | null
  band?: { lo: number; hi: number } | null
  gapFill?: boolean
  /** A dot on the last value (today's price). */
  endDot?: boolean
  className?: string
  /** Stretch to the CSS box (width/height set in CSS) without thickening the strokes. */
  fluid?: boolean
}

export function Sparkline({
  values,
  up,
  width = 150,
  height = 38,
  compare,
  band,
  gapFill = false,
  endDot = false,
  className = 'mk-spark',
  fluid = false,
}: Props) {
  // One gradient per instance (a shared id makes every row paint the first row's colour);
  // stripped to [\w-] because some engines reject useId's punctuation inside url(#…).
  const gradId = `spk${useId().replace(/[^\w-]/g, '')}`
  if (values.length < 2) return null

  const col = up ? 'var(--good)' : 'var(--bad)'
  const pad = 3
  const cmp = compare && compare.length >= 2 ? compare : null
  const all = cmp ? [...values, ...cmp] : values
  const min = Math.min(...all)
  const max = Math.max(...all)
  const rng = max - min || 1
  const y = (v: number) => height - pad - ((v - min) / rng) * (height - 2 * pad)
  const path = (vs: number[]) =>
    vs
      .map(
        (v, i) =>
          (i ? 'L' : 'M') + ((i / (vs.length - 1)) * width).toFixed(1) + ' ' + y(v).toFixed(1),
      )
      .join(' ')
  const line = path(values)
  const area = line + ` L ${width} ${height} L 0 ${height} Z`
  const stroke = fluid ? 'non-scaling-stroke' : undefined

  const gaps = gapFill && cmp && cmp.length === values.length ? gapRuns(values, cmp) : null
  const px = (x: number) => ((x / (values.length - 1)) * width).toFixed(1)
  const gapPath = (r: GapRun) => {
    const top = r.pts.map((p, i) => `${i ? 'L' : 'M'}${px(p.x)} ${y(p.v).toFixed(1)}`)
    const back = [...r.pts].reverse().map((p) => `L${px(p.x)} ${y(p.c).toFixed(1)}`)
    return [...top, ...back, 'Z'].join(' ')
  }

  let bandRect: { top: number; h: number } | null = null
  if (band && band.hi > band.lo) {
    const top = y(Math.min(max, band.hi))
    const bottom = y(Math.max(min, band.lo))
    if (bottom > top) bandRect = { top, h: bottom - top }
  }
  const lastY = y(values[values.length - 1]!).toFixed(1)

  return (
    <svg
      className={className}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio={fluid ? 'none' : undefined}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={col} stopOpacity="0.28" />
          <stop offset="1" stopColor={col} stopOpacity="0" />
        </linearGradient>
      </defs>
      {bandRect && (
        <rect className="ui-spark-band" x={0} y={bandRect.top} width={width} height={bandRect.h} />
      )}
      {gaps ? (
        gaps.map((r, i) => (
          <path
            key={i}
            className={`ui-spark-gap ui-spark-gap--${r.ahead ? 'ahead' : 'behind'}`}
            d={gapPath(r)}
          />
        ))
      ) : (
        <path d={area} fill={`url(#${gradId})`} />
      )}
      {cmp && <path className="ui-spark-compare" d={path(cmp)} fill="none" vectorEffect={stroke} />}
      <path
        d={line}
        fill="none"
        stroke={col}
        strokeWidth="1.6"
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect={stroke}
      />
      {endDot && (
        // A zero-length round-capped stroke: stays a circle even when a fluid chart is stretched.
        <path
          className="ui-spark-dot"
          d={`M${width} ${lastY} L${width} ${lastY}`}
          stroke={col}
          strokeWidth="4.5"
          strokeLinecap="round"
          vectorEffect={stroke}
        />
      )}
    </svg>
  )
}
