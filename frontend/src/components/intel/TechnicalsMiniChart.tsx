import { useEffect, useRef, useState } from 'react'
import {
  createChart,
  LineSeries,
  HistogramSeries,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type Time,
} from 'lightweight-charts'
import { toTime } from './assetPriceChartLwc'
import { useTheme } from '../../theme'

export interface TechOverlayPt {
  date: string
  value: number
}

export interface TechOverlays {
  sma_50?: TechOverlayPt[]
  sma_200?: TechOverlayPt[]
  bb_mid?: TechOverlayPt[]
  bb_upper?: TechOverlayPt[]
  bb_lower?: TechOverlayPt[]
  macd?: TechOverlayPt[]
  rsi?: TechOverlayPt[]
}

interface Props {
  points: { date: string; price: number }[]
  overlays: TechOverlays
  timeframe: string
}

type LegendItem = {
  key: string
  label: string
  color: string
  dashed?: boolean
  swatch?: 'bar'
  toggle?: boolean
  active?: boolean
}

/** Multi-pane Technicals chart: Price+SMAs (+ optional Bollinger), MACD, RSI. */
export function TechnicalsMiniChart({ points, overlays, timeframe }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const [showBollinger, setShowBollinger] = useState(false)
  // A dependency of the recolour effect below, so a light<->dark switch repaints the
  // EXISTING chart (same fault as AssetPriceChart's gridlines, owner-reproduced 2026-09-20:
  // every colour here was read once at chart creation).
  const { resolved: themeName } = useTheme()

  const hasMacd = !!overlays.macd?.length
  const hasRsi = !!overlays.rsi?.length
  const hasBb = !!(
    overlays.bb_upper?.length ||
    overlays.bb_lower?.length ||
    overlays.bb_mid?.length
  )

  useEffect(() => {
    const el = hostRef.current
    if (!el || points.length < 2) return

    const styles = getComputedStyle(document.documentElement)
    const lineColor = styles.getPropertyValue('--ink').trim() || '#1a1a1a'
    const muted = styles.getPropertyValue('--faint').trim() || '#999'
    const good = styles.getPropertyValue('--good').trim() || '#2a7a4b'
    const bad = styles.getPropertyValue('--bad').trim() || '#b33'
    const accent = styles.getPropertyValue('--accent').trim() || '#4a90d9'
    const warn = styles.getPropertyValue('--warn').trim() || '#c9a227'
    const surface = styles.getPropertyValue('--surface').trim() || '#fff'
    const grid = styles.getPropertyValue('--line').trim() || '#e5e5e5'

    const paneCount = 1 + (hasMacd ? 1 : 0) + (hasRsi ? 1 : 0)
    const height = paneCount === 1 ? 200 : paneCount === 2 ? 280 : 340

    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: surface },
        textColor: muted,
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: grid },
        horzLines: { color: grid },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: timeframe === '1M' },
      crosshair: { mode: CrosshairMode.Magnet },
    })
    chartRef.current = chart

    // Pane 0 — price + SMAs; Bollinger only when toggled on via legend.
    const price = chart.addSeries(
      LineSeries,
      {
        color: lineColor,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: '',
      },
      0,
    )
    price.setData(points.map((p) => ({ time: toTime(p.date) as Time, value: p.price })))

    const addLine = (
      pts: TechOverlayPt[] | undefined,
      opts: {
        color: string
        width?: 1 | 2
        dashed?: boolean
        pane?: number
      },
    ) => {
      if (!pts?.length) return
      const s = chart.addSeries(
        LineSeries,
        {
          color: opts.color,
          lineWidth: opts.width ?? 1,
          lineStyle: opts.dashed ? 2 : 0,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          title: '',
        },
        opts.pane ?? 0,
      )
      s.setData(pts.map((p) => ({ time: toTime(p.date) as Time, value: p.value })))
      return s
    }

    addLine(overlays.sma_50, { color: good, width: 1 })
    addLine(overlays.sma_200, { color: muted, width: 1, dashed: true })
    if (showBollinger) {
      addLine(overlays.bb_mid, { color: warn, width: 1, dashed: true })
      addLine(overlays.bb_upper, { color: muted, width: 1, dashed: true })
      addLine(overlays.bb_lower, { color: muted, width: 1, dashed: true })
    }

    let nextPane = 1
    if (hasMacd && overlays.macd) {
      const macdPane = nextPane++
      const hist = chart.addSeries(
        HistogramSeries,
        {
          priceLineVisible: false,
          lastValueVisible: true,
          title: '',
          priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        },
        macdPane,
      )
      hist.setData(
        overlays.macd.map((p) => ({
          time: toTime(p.date) as Time,
          value: p.value,
          color: p.value >= 0 ? good : bad,
        })),
      )
    }

    if (hasRsi && overlays.rsi) {
      const rsiPane = nextPane++
      const rsi = chart.addSeries(
        LineSeries,
        {
          color: accent,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: true,
          title: '',
          priceFormat: { type: 'price', precision: 0, minMove: 1 },
          autoscaleInfoProvider: () => ({
            priceRange: { minValue: 0, maxValue: 100 },
          }),
        },
        rsiPane,
      )
      rsi.setData(overlays.rsi.map((p) => ({ time: toTime(p.date) as Time, value: p.value })))
      rsi.createPriceLine({
        price: 70,
        color: muted,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
        title: '',
      })
      rsi.createPriceLine({
        price: 30,
        color: muted,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
        title: '',
      })
    }

    const panes = chart.panes()
    if (panes[0]) panes[0].setStretchFactor(paneCount === 1 ? 1 : 3)
    for (let i = 1; i < panes.length; i++) panes[i]?.setStretchFactor(1)

    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (hostRef.current) chart.applyOptions({ width: hostRef.current.clientWidth })
    })
    ro.observe(el)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [points, overlays, timeframe, hasMacd, hasRsi, showBollinger, themeName])

  const legend: LegendItem[] = [
    { key: 'price', label: 'Price', color: 'var(--ink)' },
    ...(overlays.sma_50?.length
      ? [{ key: 'sma50', label: 'SMA 50', color: 'var(--good)' } satisfies LegendItem]
      : []),
    ...(overlays.sma_200?.length
      ? [
          {
            key: 'sma200',
            label: 'SMA 200',
            color: 'var(--faint)',
            dashed: true,
          } satisfies LegendItem,
        ]
      : []),
    ...(hasBb
      ? [
          {
            key: 'bb',
            label: 'Bollinger',
            color: 'var(--warn)',
            dashed: true,
            toggle: true,
            active: showBollinger,
          } satisfies LegendItem,
        ]
      : []),
    ...(hasMacd
      ? [
          {
            key: 'macd',
            label: 'MACD',
            color: 'var(--good)',
            swatch: 'bar' as const,
          } satisfies LegendItem,
        ]
      : []),
    ...(hasRsi
      ? [{ key: 'rsi', label: 'RSI (14)', color: 'var(--accent)' } satisfies LegendItem]
      : []),
  ]

  return (
    <div className="asd-tech-chart-wrap">
      <div className="asd-tech-legend" aria-label="Chart series">
        {legend.map((item) =>
          item.toggle ? (
            <button
              key={item.key}
              type="button"
              className={
                item.active
                  ? 'asd-tech-legend-item asd-tech-legend-item--toggle asd-tech-legend-item--on'
                  : 'asd-tech-legend-item asd-tech-legend-item--toggle'
              }
              aria-pressed={!!item.active}
              onClick={() => setShowBollinger((v) => !v)}
            >
              <span
                className="asd-tech-legend-swatch asd-tech-legend-swatch--dashed"
                style={{ color: item.color }}
              />
              {item.label}
            </button>
          ) : (
            <span key={item.key} className="asd-tech-legend-item">
              <span
                className={
                  item.swatch === 'bar'
                    ? 'asd-tech-legend-swatch asd-tech-legend-swatch--bar'
                    : item.dashed
                      ? 'asd-tech-legend-swatch asd-tech-legend-swatch--dashed'
                      : 'asd-tech-legend-swatch'
                }
                style={
                  item.swatch === 'bar'
                    ? undefined
                    : item.dashed
                      ? { color: item.color }
                      : { background: item.color }
                }
              />
              {item.label}
            </span>
          ),
        )}
      </div>
      <div ref={hostRef} className="asd-tech-chart" />
    </div>
  )
}
