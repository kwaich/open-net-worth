import { useEffect, useState, type ComponentType } from 'react'
import { Button } from '../ui'
import {
  ChevronDown,
  ChartLine,
  ChartArea,
  Baseline,
  ChartCandlestick,
  ChartBar,
  type IconProps,
} from '../../ui/icons'

export type ChartType = 'line' | 'candle' | 'baseline' | 'mountain' | 'bar'

const STORAGE_KEY = 'asset-chart-type'

interface ChartTypeDef {
  value: ChartType
  label: string
  Icon: ComponentType<IconProps>
  /** True for chart types that need real OHLC bars (Candle/Bar) — disabled in the menu
   *  when the current timeframe/asset combo can't supply them. */
  needsOhlc: boolean
}

// Yahoo Finance's own dropdown order — Line/Candle/Baseline/Mountain/Bar.
const CHART_TYPE_DEFS: ChartTypeDef[] = [
  { value: 'line', label: 'Line', Icon: ChartLine, needsOhlc: false },
  { value: 'candle', label: 'Candle', Icon: ChartCandlestick, needsOhlc: true },
  { value: 'baseline', label: 'Baseline', Icon: Baseline, needsOhlc: false },
  { value: 'mountain', label: 'Mountain', Icon: ChartArea, needsOhlc: false },
  { value: 'bar', label: 'Bar', Icon: ChartBar, needsOhlc: true },
]

function isChartType(v: string | null): v is ChartType {
  return v === 'line' || v === 'candle' || v === 'baseline' || v === 'mountain' || v === 'bar'
}

/** Last-used chart type — a single GLOBAL key (not per-instrument): the pick sticks
 *  across every asset and session, per the owner's decision. Defaults to Line. */
export function readStoredChartType(): ChartType {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    return isChartType(saved) ? saved : 'line'
  } catch {
    return 'line'
  }
}

function storeChartType(v: ChartType): void {
  try {
    localStorage.setItem(STORAGE_KEY, v)
  } catch {
    // Private browsing / quota-exceeded — the in-memory selection still works this session.
  }
}

export interface ToolbarMenuItem<V extends string> {
  value: V
  label: string
  Icon?: ComponentType<IconProps>
  disabled?: boolean
  /** Hover explanation shown on a disabled row. */
  disabledTitle?: string
}

interface ToolbarMenuProps<V extends string> {
  ariaLabel: string
  value: V
  items: ToolbarMenuItem<V>[]
  onPick: (v: V) => void
  /** Extra class(es) on the wrapper — e.g. the narrow-pane visibility toggle. */
  className?: string
}

/** Shared chart-toolbar dropdown (ghost trigger + overlay + menu list, mirroring
 *  AssetWatchlistMenu's shape) — used by the chart-type picker and, on a narrow
 *  chart pane, the timeframe picker, so the two read as one control family. */
export function ToolbarMenu<V extends string>({
  ariaLabel,
  value,
  items,
  onPick,
  className,
}: ToolbarMenuProps<V>) {
  const [open, setOpen] = useState(false)
  const active = items.find((d) => d.value === value) ?? items[0]!

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  function pick(v: V) {
    onPick(v)
    setOpen(false)
  }

  return (
    <div className={`asd-cht${className ? ` ${className}` : ''}`}>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={ariaLabel}
      >
        {active.Icon && <active.Icon size={14} />}
        {active.label}
        <ChevronDown size={13} className="asd-cht-caret" />
      </Button>
      {open && (
        <>
          <div className="asd-cht-overlay" onClick={() => setOpen(false)} />
          <div className="asd-cht-menu" role="menu">
            {items.map((d) => (
              <button
                key={d.value}
                type="button"
                className={`asd-cht-row${d.value === value ? ' asd-cht-row--active' : ''}`}
                role="menuitemradio"
                aria-checked={d.value === value}
                disabled={d.disabled}
                title={d.disabled ? d.disabledTitle : undefined}
                onClick={() => pick(d.value)}
              >
                {d.Icon && <d.Icon size={14} className="asd-cht-icon" />}
                <span className="asd-cht-label">{d.label}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

interface Props {
  value: ChartType
  onChange: (v: ChartType) => void
  /** False disables the Candle/Bar rows (no OHLC data for this timeframe/asset). */
  ohlcAvailable: boolean
}

/** Chart-type picker, anchored to the toolbar's right edge. */
export function ChartTypeMenu({ value, onChange, ohlcAvailable }: Props) {
  return (
    <ToolbarMenu
      className="asd-cht--right"
      ariaLabel="Chart type"
      value={value}
      items={CHART_TYPE_DEFS.map((d) => ({
        value: d.value,
        label: d.label,
        Icon: d.Icon,
        disabled: d.needsOhlc && !ohlcAvailable,
        disabledTitle: 'No OHLC data for this range',
      }))}
      onPick={(v) => {
        storeChartType(v)
        onChange(v)
      }}
    />
  )
}
