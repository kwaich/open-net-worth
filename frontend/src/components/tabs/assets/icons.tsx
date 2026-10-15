// Assets tab icon set. The 7 category glyphs (property/vehicle/watch/art/metal/policy/other)
// render the app's shared lucide-react icon set, not hand-drawn paths — every other icon here
// is a verbatim port of the design handoff's Icons map (design_handoffs/assets_handoff/
// prototype/assets_tab/data.jsx, lines 3-24; same svg attributes: viewBox 0 0 24 24, stroke
// currentColor, strokeWidth 1.6, round caps/joins, 20×20 default). Each icon is a plain
// function (not a JSX component tag) so callers can do `Icons[a.category] ?? Icons.other`
// and invoke it directly, exactly like the prototype's `Icons.pencil({ width: 13, height: 13 })`
// — the category glyphs keep that same call shape, just rendering a lucide icon inside.
import type { ComponentType, CSSProperties, JSX } from 'react'
import {
  Box,
  Car,
  Coins,
  CreditCard,
  HandCoins,
  Home,
  Palette,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Watch as WatchGlyph,
} from 'lucide-react'

export interface IconProps {
  width?: number
  height?: number
  style?: CSSProperties
}

type IconFn = (p?: IconProps) => JSX.Element

/** The shared stroke pack every hand-drawn icon below uses. */
function strokeSvg(
  body: JSX.Element[],
  { width = 20, height = 20, style }: IconProps,
  overrides?: { fill?: string; stroke?: string },
) {
  return (
    <svg
      width={width}
      height={height}
      viewBox="0 0 24 24"
      fill={overrides?.fill ?? 'none'}
      stroke={overrides?.stroke ?? 'currentColor'}
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={style}
    >
      {body}
    </svg>
  )
}

/** A category glyph is still an `IconFn` — same `{width, height, style}` call shape as every
 *  other icon here — it just renders a lucide-react component instead of a hand-drawn path. */
function libraryIcon(
  Glyph: ComponentType<{ size?: number; style?: CSSProperties; strokeWidth?: number }>,
) {
  const fn: IconFn = ({ width = 20, style }: IconProps = {}) => (
    <Glyph size={width} style={style} strokeWidth={1.6} />
  )
  return fn
}

const property = libraryIcon(Home)
const vehicle = libraryIcon(Car)
const watch = libraryIcon(WatchGlyph)
const art = libraryIcon(Palette)
const metal = libraryIcon(Coins)
const policy = libraryIcon(ShieldCheck)
const debt = libraryIcon(HandCoins)
const other = libraryIcon(Box)
const trash = libraryIcon(Trash2)
const refresh = libraryIcon(RefreshCw)
const card = libraryIcon(CreditCard)

const check: IconFn = ({ width = 14, height = 14, style }: IconProps = {}) =>
  strokeSvg([<path key="1" d="M4.5 12.5l4.5 4.5L19.5 6.5" />], { width, height, style })

const clock: IconFn = ({ width = 13, height = 13, style }: IconProps = {}) =>
  strokeSvg([<circle key="1" cx="12" cy="12" r="8.5" />, <path key="2" d="M12 7.5V12l3 1.8" />], {
    width,
    height,
    style,
  })

const bolt: IconFn = ({ width = 13, height = 13, style }: IconProps = {}) =>
  strokeSvg([<path key="1" d="M13 3L6 13h5l-1 8 8-10h-5z" />], { width, height, style })

const plus: IconFn = ({ width = 15, height = 15, style }: IconProps = {}) =>
  strokeSvg([<path key="1" d="M12 5v14" />, <path key="2" d="M5 12h14" />], {
    width,
    height,
    style,
  })

const doc: IconFn = ({ width = 13, height = 13, style }: IconProps = {}) =>
  strokeSvg([<path key="1" d="M7 3h7l4 4v14H7z" />, <path key="2" d="M14 3v4h4" />], {
    width,
    height,
    style,
  })

const link: IconFn = ({ width = 13, height = 13, style }: IconProps = {}) =>
  strokeSvg(
    [
      <path key="1" d="M10 13.5a3.5 3.5 0 0 0 5 0l2.5-2.5a3.5 3.5 0 0 0-5-5L11 7.5" />,
      <path key="2" d="M14 10.5a3.5 3.5 0 0 0-5 0L6.5 13a3.5 3.5 0 0 0 5 5l1.5-1.5" />,
    ],
    { width, height, style },
  )

const pencil: IconFn = ({ width = 15, height = 15, style }: IconProps = {}) =>
  strokeSvg([<path key="1" d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17z" />], { width, height, style })

const cash: IconFn = (p = {}) =>
  strokeSvg(
    [
      <rect key="1" x="3" y="6.5" width="18" height="11" rx="2" />,
      <circle key="2" cx="12" cy="12" r="2.6" />,
      <path key="3" d="M6.5 10v4" />,
      <path key="4" d="M17.5 10v4" />,
    ],
    p,
  )

const investments: IconFn = (p = {}) =>
  strokeSvg(
    [
      <path key="1" d="M4 18V6" />,
      <path key="2" d="M4 18h16" />,
      <path key="3" d="M7.5 18v-4.5" />,
      <path key="4" d="M11.5 18V9.5" />,
      <path key="5" d="M15.5 18v-7" />,
      <path key="6" d="M19 18V7.5" />,
    ],
    p,
  )

const retirement: IconFn = (p = {}) =>
  strokeSvg(
    [
      <path key="1" d="M3 12a9 9 0 0 1 18 0Z" />,
      <path key="2" d="M12 12v8" />,
      <path key="3" d="M12 20a2 2 0 0 1-4 0" />,
      <path key="4" d="M12 3v1.5" />,
    ],
    p,
  )

const receivable: IconFn = (p = {}) =>
  strokeSvg(
    [
      <path key="1" d="M4 12h4l2 3h4l2-3h4" />,
      <path key="2" d="M4 12V6.5a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1V12" />,
      <path key="3" d="M4 12v5.5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V12" />,
    ],
    p,
  )

/** Category + chrome icons, keyed exactly like the prototype's `window.AS.Icons` — a category
 *  render site does `Icons[a.category] ?? Icons.other`. */
export const Icons: Record<string, IconFn> = {
  property,
  vehicle,
  watch,
  art,
  metal,
  policy,
  other,
  check,
  clock,
  bolt,
  plus,
  doc,
  link,
  pencil,
  cash,
  investments,
  retirement,
  receivable,
  debt,
  card,
  trash,
  refresh,
}
