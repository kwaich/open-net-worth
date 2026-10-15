import { describe, expect, it } from 'vitest'
import { groupByAge } from './ConversationMenu'
import type { ConversationMeta } from '../../hooks/useConversations'

// "Yesterday" is how people recall a conversation. Six identically-shaped rows each
// carrying a timestamp is a table nobody reads.

const ago = (ms: number) => new Date(Date.now() - ms).toISOString()
const conv = (over: Partial<ConversationMeta>): ConversationMeta => ({
  id: 'c',
  title: 't',
  createdAt: ago(0),
  updatedAt: ago(0),
  messageCount: 1,
  model: '',
  ...over,
})

const HOUR = 3_600_000
const DAY = 86_400_000

describe('groupByAge', () => {
  it('labels the buckets by how you would say it out loud', () => {
    const groups = groupByAge([
      conv({ id: 'now', updatedAt: ago(2 * HOUR) }),
      conv({ id: 'yesterday', updatedAt: ago(30 * HOUR) }),
      conv({ id: 'week', updatedAt: ago(4 * DAY) }),
      conv({ id: 'old', updatedAt: ago(40 * DAY) }),
    ])

    expect(groups.map((g) => g.label)).toEqual(['Today', 'Yesterday', 'This past week', 'Older'])
  })

  it('omits a bucket with nothing in it', () => {
    const groups = groupByAge([conv({ updatedAt: ago(2 * HOUR) })])

    expect(groups.map((g) => g.label)).toEqual(['Today'])
  })

  it('keeps the order it was given inside a bucket', () => {
    const groups = groupByAge([
      conv({ id: 'a', updatedAt: ago(HOUR) }),
      conv({ id: 'b', updatedAt: ago(2 * HOUR) }),
    ])

    expect(groups[0]!.items.map((c) => c.id)).toEqual(['a', 'b'])
  })

  it('treats an unusable timestamp as oldest rather than crashing or claiming today', () => {
    const groups = groupByAge([conv({ id: 'broken', createdAt: '', updatedAt: 'nonsense' })])

    expect(groups[0]!.label).toBe('Older')
  })

  it('returns nothing for an empty list', () => {
    expect(groupByAge([])).toEqual([])
  })
})
