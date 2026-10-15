// Overview v2 — the viz (chart-type) model: a widget's data SHAPE, not its
// identity, decides which chart-type representations are valid (Grafana-
// style). Ported from the handoff's `app.jsx` VIZ_BY_SHAPE/vizOf. The choice is
// stored per-instance at `config.viz` and read the same way by every widget of
// that shape — the reason a Lucius-built widget just declares a shape and
// inherits the same control, with no per-widget wiring.

import type { VizOption, WidgetDefinition, WidgetInstance, WidgetShape } from './types'

export const VIZ_BY_SHAPE: Record<WidgetShape, VizOption[]> = {
  breakdown: [
    { id: 'list', label: 'List' },
    { id: 'bar', label: 'Bar' },
    { id: 'pie', label: 'Pie' },
  ],
  'nw-headline': [
    { id: 'full', label: 'Chart + numbers' },
    { id: 'chart', label: 'Chart' },
    { id: 'numbers', label: 'Numbers' },
  ],
  'nw-chart': [
    { id: 'trend', label: 'Trend' },
    { id: 'comp', label: 'Composition' },
  ],
  lux: [
    { id: 'bar', label: 'Bar' },
    { id: 'pie', label: 'Pie' },
    { id: 'line', label: 'Line' },
    { id: 'treemap', label: 'Treemap' },
    { id: 'list', label: 'List' },
  ],
}

export function vizOptionsFor(def?: WidgetDefinition): VizOption[] | null {
  return (def?.shape && VIZ_BY_SHAPE[def.shape]) || null
}

export function vizOf(
  instance: WidgetInstance | undefined,
  def: WidgetDefinition | undefined,
): string | null {
  const configured = instance?.config?.viz
  if (typeof configured === 'string') return configured
  const opts = vizOptionsFor(def)
  return opts?.[0]?.id ?? null
}
