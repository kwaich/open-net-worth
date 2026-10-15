import { describe, it, expect } from 'vitest'
import { serializeSnapshot } from './screenSnapshot'
import type { ScreenSnapshot } from './screenSnapshot'

const snap: ScreenSnapshot = {
  id: 'asset-detail',
  title: 'NVIDIA Corporation',
  viewState: { tab: 'dashboard' },
  facts: { price: '170 USD', today: '+3.1%' },
  visibleText: 'lots of text',
  handles: [
    {
      id: 'news-0',
      type: 'news_story',
      label: 'Reuters',
      descriptor: 'NVDA beats',
      source: 'https://x.com/a',
      icon: '◴',
    },
    {
      id: 'price-1y',
      type: 'price_series',
      label: 'Price history – 1Y',
      source: 'price_series:inst_1:1Y',
    },
  ],
  asOf: '2026-06-24T10:00',
}

describe('serializeSnapshot', () => {
  it('maps camelCase view-model → snake_case wire shape', () => {
    const w = serializeSnapshot(snap)
    expect(w.id).toBe('asset-detail')
    expect(w.view_state).toEqual({ tab: 'dashboard' })
    expect(w.visible_text).toBe('lots of text')
    expect(w.as_of).toBe('2026-06-24T10:00')
    const h0 = w.handles[0]!
    expect(h0).toEqual({
      id: 'news-0',
      type: 'news_story',
      label: 'Reuters',
      descriptor: 'NVDA beats',
      source: 'https://x.com/a',
      pinned: false,
    })
    // icon is UI-only — never shipped to the backend
    expect('icon' in h0).toBe(false)
  })

  it('marks pinned handles (the "+ attach" set)', () => {
    const w = serializeSnapshot(snap, ['price-1y'])
    expect(w.handles.find((h) => h.id === 'price-1y')!.pinned).toBe(true)
    expect(w.handles.find((h) => h.id === 'news-0')!.pinned).toBe(false)
  })

  it('defaults missing optional fields', () => {
    const w = serializeSnapshot({ id: 'x', title: 'X' })
    expect(w.view_state).toEqual({})
    expect(w.facts).toEqual({})
    expect(w.visible_text).toBe('')
    expect(w.handles).toEqual([])
    expect(w.as_of).toBeNull()
  })
})
