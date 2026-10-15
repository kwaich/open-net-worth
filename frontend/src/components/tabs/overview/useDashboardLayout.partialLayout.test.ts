// A saved view whose widgets carry only a `defId` must still render.
//
// Regression (real report, 2026-08-23): the seeded demo dashboard stored widgets as
// `{defId, config}` and left geometry to the client. The load path ran `compact()`, which
// only resolves overlaps between items that ALREADY have geometry — so `w`/`h` stayed
// undefined all the way to GridCard, which computes `width: calc(${(w/12)*100}% - 16px)`
// → `calc(NaN%)` and `top: undefined`. Every tile collapsed to content width and stacked at
// one point: the Overview rendered as a single narrow sliver.
//
// The fix is `normalize()` on load — it fills each missing field from the widget's own
// definition, then compacts. This test drives the real functions, not a copy of them.

import { describe, expect, it } from 'vitest'
import { compact, normalize } from './grid'
import { WIDGET_DEFINITIONS } from './definitions'
import { isSection, type WidgetInstance } from './types'

/** `normalize` yields widgets and section dividers; these tests only place widgets. */
const normalizeWidgets = (saved: unknown): WidgetInstance[] =>
  normalize(saved, WIDGET_DEFINITIONS).filter((i): i is WidgetInstance => !isSection(i))

type SeededView = {
  name: string
  widgets: Array<{ defId: string; x: number; y: number; w: number; h: number }>
}

const PARTIAL = ['hero', 'attention', 'briefing', 'upcoming', 'alloc', 'reach', 'venues'].map(
  (defId) => ({ defId, config: {}, instanceId: `widg_${defId}` }),
)

describe('a partially-specified saved layout', () => {
  it('reaches the grid with real geometry on every widget', () => {
    const items = normalizeWidgets(PARTIAL)

    expect(items).toHaveLength(PARTIAL.length)
    for (const item of items) {
      expect(Number.isFinite(item.w), `${item.defId} w`).toBe(true)
      expect(Number.isFinite(item.h), `${item.defId} h`).toBe(true)
      expect(Number.isFinite(item.x), `${item.defId} x`).toBe(true)
      expect(Number.isFinite(item.y), `${item.defId} y`).toBe(true)
      expect(item.w).toBeGreaterThan(0)
      expect(item.h).toBeGreaterThan(0)
    }
  })

  it('does not all collapse onto one point', () => {
    const items = normalizeWidgets(PARTIAL)
    const spots = new Set(items.map((i) => `${i.x},${i.y}`))
    expect(spots.size).toBeGreaterThan(1)
  })

  it('is the specific thing compact() alone does NOT do', () => {
    // Pins WHY the load path cannot go back to `compact` — the bug, stated as a test.
    const compacted = compact(PARTIAL as never)
    expect(compacted.some((i) => !Number.isFinite((i as { w?: number }).w))).toBe(true)
  })

  it('leaves a fully-specified layout alone', () => {
    const explicit = [{ defId: 'hero', instanceId: 'a', config: {}, x: 0, y: 0, w: 6, h: 200 }]
    const [item] = normalizeWidgets(explicit)
    expect([item?.x, item?.y, item?.w, item?.h]).toEqual([0, 0, 6, 200])
  })
})

// ── the seeded demo layout is a BAKED snapshot; keep it honest ────────────────

describe('the demo`s seeded dashboard layout', () => {
  it('still matches what the live widget definitions would compute', async () => {
    // backend/scripts/demo/dashboard_layout.json was produced by running `normalize` over
    // these widget lists. Baking it lets the seeder store a layout the API accepts and the
    // grid can place — but a baked snapshot goes stale the moment a widget definition
    // changes its default size, and nothing else would notice. This re-derives it.
    const seeded = (await import('../../../../../backend/scripts/demo/dashboard_layout.json'))
      .default as SeededView[]

    for (const view of seeded) {
      const derived = normalizeWidgets(view.widgets.map((w) => ({ defId: w.defId })))
      expect(derived.map((d) => [d.defId, d.x, d.y, d.w, d.h])).toEqual(
        view.widgets.map((w) => [w.defId, w.x, w.y, w.w, w.h]),
      )
    }
  })

  it('gives every seeded widget geometry the grid can actually place', async () => {
    const seeded = (await import('../../../../../backend/scripts/demo/dashboard_layout.json'))
      .default as SeededView[]

    expect(seeded.length).toBeGreaterThan(0)
    for (const view of seeded) {
      expect(view.widgets.length).toBeGreaterThan(0)
      for (const w of view.widgets) {
        for (const k of ['x', 'y', 'w', 'h'] as const) {
          expect(Number.isFinite(w[k]), `${w.defId}.${k}`).toBe(true)
        }
      }
    }
  })
})
