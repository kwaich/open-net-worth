import { describe, it, expect } from 'vitest'
import { storyScope } from './intelScope'
import type { StoryArc, StoryType } from './storyMeta'

function story(id: string, type: StoryType, ticker?: string): StoryArc {
  return {
    id,
    type,
    ticker,
    label: id,
    status: 'active',
    summary: '',
    action_items: [],
    timeline: [],
    created_at: '',
    updated_at: '',
  }
}

const HELD = new Set(['MSFT', 'KSTL.DE'])
const WATCHED = new Set(['NVDA'])

describe('storyScope', () => {
  it('puts a held ticker on Holdings whatever its type', () => {
    expect(storyScope(story('a', 'asset', 'msft'), HELD, WATCHED)).toBe('holdings')
    expect(storyScope(story('b', 'opportunity', 'KSTL.DE'), HELD, WATCHED)).toBe('holdings')
  })
  it('puts a watched ticker on Watching', () => {
    expect(storyScope(story('c', 'asset', 'NVDA'), HELD, WATCHED)).toBe('watching')
    expect(storyScope(story('d', 'price_alert', 'nvda'), HELD, WATCHED)).toBe('watching')
  })
  it('keeps portfolio-level risk and alerts on Holdings', () => {
    expect(storyScope(story('e', 'risk'), HELD, WATCHED)).toBe('holdings')
    expect(storyScope(story('f', 'price_alert', 'OLD'), HELD, WATCHED)).toBe('holdings')
  })
  it('sends macro, topics and unrelated tickers to Macro & topics', () => {
    expect(storyScope(story('g', 'macro'), HELD, WATCHED)).toBe('macro')
    expect(storyScope(story('h', 'topic'), HELD, WATCHED)).toBe('macro')
    expect(storyScope(story('i', 'opportunity', 'PLTR'), HELD, WATCHED)).toBe('macro')
  })
  it('falls back to Holdings for a tickerless update', () => {
    expect(storyScope(story('j', 'asset'), HELD, WATCHED)).toBe('holdings')
  })
})
