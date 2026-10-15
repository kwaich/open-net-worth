import { describe, expect, it } from 'vitest'
import { PRESET_VIEWS, WIDGET_DEFINITIONS } from './definitions'
import { COLS, seedWidgets } from './grid'
import type { WidgetInstance } from './types'

const seeded = (name: string) =>
  seedWidgets(
    PRESET_VIEWS.find((v) => v.name === name)!.widgets,
    WIDGET_DEFINITIONS,
  ) as WidgetInstance[]

describe('PRESET_VIEWS', () => {
  it('seeds only Overview and Reports — the other screens already own the rest', () => {
    expect(PRESET_VIEWS.map((v) => v.name)).toEqual(['Overview', 'Reports'])
  })

  it.each(['Overview', 'Reports'])('lays %s out in full rows, no gap at a row end', (name) => {
    const widgets = seeded(name)
    expect(widgets).toHaveLength(PRESET_VIEWS.find((v) => v.name === name)!.widgets.length)
    const rows = new Map<number, number>()
    for (const w of widgets) rows.set(w.y, (rows.get(w.y) ?? 0) + w.w)
    for (const width of rows.values()) expect(width).toBe(COLS)
  })

  it("leaves Lucius's daily briefing off the starter Overview", () => {
    expect(seeded('Overview').some((w) => w.defId === 'briefing')).toBe(false)
    expect(WIDGET_DEFINITIONS.find((d) => d.defId === 'briefing')!.hidden).toBeFalsy()
  })

  it('puts the Subscriptions widget on the Overview, set to the starter category', () => {
    const subs = seeded('Overview').find((w) => w.defId === 'subscriptions')!
    expect(subs.config.categories).toEqual(['cat_f2ff09940000'])
  })

  it('names the Reports Sankey "Money flow", not a second "Cash flow"', () => {
    const titles = WIDGET_DEFINITIONS.filter((d) => !d.hidden).map((d) => d.title)
    expect(titles.filter((t) => t === 'Cash flow')).toHaveLength(1)
    expect(WIDGET_DEFINITIONS.find((d) => d.defId === 'rep-sankey')!.title).toBe('Money flow')
  })
})
