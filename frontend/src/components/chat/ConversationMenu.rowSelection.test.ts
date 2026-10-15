// selectVisibleRows / sortWithinSection — concurrent-conversations-ui-spec.md §5.1.1.
//
// The part that makes E6 actually "loudest, not absent": a dot on the row means nothing if
// the row itself can silently scroll out of view behind five newer, boring rows. Pinned
// here without rendering React — both functions are plain array transforms over
// ConversationMeta[] + a statusFor lookup.

import { describe, it, expect } from 'vitest'
import { selectVisibleRows, sortWithinSection } from './ConversationMenu'
import type { ConversationMeta } from '../../hooks/useConversations'
import type { ConvRowStatus } from '../../types/chat'

function conv(id: string, minutesAgo: number): ConversationMeta {
  const at = new Date(Date.now() - minutesAgo * 60_000).toISOString()
  return { id, title: id, createdAt: at, updatedAt: at, messageCount: 1, model: '' }
}

describe('selectVisibleRows — §5.1.1 rule 2 (force-inclusion past the cap)', () => {
  it('a needs_you row outside the top-N by recency still appears — it is never pushed off', () => {
    // 6 rows, newest first; the OLDEST (index 5, well past a cap of 5) is needs_you. It
    // takes ONE of the 5 slots — bumping out the 5th-most-recent idle row ('e') — the
    // section stays capped in spirit (§5.1.1: "here's what's active, plus a few recents").
    const items = [
      conv('a', 1),
      conv('b', 2),
      conv('c', 3),
      conv('d', 4),
      conv('e', 5),
      conv('stuck', 60),
    ]
    const statusMap: Record<string, ConvRowStatus> = { stuck: 'needs_you' }
    const statusFor = (id: string) => statusMap[id] ?? 'idle'

    const visible = selectVisibleRows(items, statusFor, 5)

    expect(visible.map((c) => c.id)).toContain('stuck')
    expect(visible.map((c) => c.id)).not.toContain('e')
    expect(visible.length).toBe(5)
  })

  it('a working row survives the cap the same way', () => {
    const items = [
      conv('a', 1),
      conv('b', 2),
      conv('c', 3),
      conv('d', 4),
      conv('e', 5),
      conv('stuck', 90),
    ]
    const statusFor = (id: string) => (id === 'stuck' ? 'working' : 'idle')

    expect(selectVisibleRows(items, statusFor, 5).map((c) => c.id)).toContain('stuck')
  })

  it('an all-idle list is simply capped at N, as before', () => {
    const items = [
      conv('a', 1),
      conv('b', 2),
      conv('c', 3),
      conv('d', 4),
      conv('e', 5),
      conv('f', 6),
    ]
    const statusFor = () => 'idle' as ConvRowStatus

    const visible = selectVisibleRows(items, statusFor, 5)
    expect(visible.length).toBe(5)
    expect(visible.map((c) => c.id)).toEqual(['a', 'b', 'c', 'd', 'e'])
  })

  it('the section only grows PAST the cap when urgent rows alone outnumber it — the rare case', () => {
    // 6 simultaneously urgent rows, cap 5: every one of them is forced in — there are no
    // fill slots left, and the section is correctly 6, not 5 ("rare... you would need more
    // than 5 simultaneously blocked/working conversations", §5.1.1).
    const items = [
      conv('stuck1', 10),
      conv('stuck2', 20),
      conv('stuck3', 30),
      conv('stuck4', 40),
      conv('stuck5', 50),
      conv('stuck6', 60),
    ]
    const statusFor = () => 'needs_you' as ConvRowStatus

    const visible = selectVisibleRows(items, statusFor, 5)
    expect(visible.length).toBe(6)
    expect(visible.map((c) => c.id)).toEqual(items.map((c) => c.id))
  })

  it('multiple urgent rows all survive, still WITHIN the cap when they do not exceed it', () => {
    const items = [
      conv('a', 1),
      conv('b', 2),
      conv('c', 3),
      conv('d', 4),
      conv('e', 5),
      conv('stuck1', 60),
      conv('stuck2', 70),
      conv('stuck3', 80),
    ]
    const statusMap: Record<string, ConvRowStatus> = {
      stuck1: 'needs_you',
      stuck2: 'working',
      stuck3: 'needs_you',
    }
    const statusFor = (id: string) => statusMap[id] ?? 'idle'

    const visible = selectVisibleRows(items, statusFor, 5)
    expect(visible.map((c) => c.id)).toEqual(expect.arrayContaining(['stuck1', 'stuck2', 'stuck3']))
    // 3 forced + 2 fill slots (5 - 3) = 5 total, not 8 — the cap absorbs them first.
    expect(visible.length).toBe(5)
  })

  it('preserves the incoming recency order (does not itself reorder for display)', () => {
    const items = [conv('a', 1), conv('stuck', 40), conv('b', 2)]
    const statusFor = (id: string) => (id === 'stuck' ? 'needs_you' : 'idle')

    // Input order is [a, stuck, b] (already recency-sorted upstream) — selectVisibleRows
    // must not itself shuffle it; sortWithinSection is the separate pass that does.
    expect(selectVisibleRows(items, statusFor, 5).map((c) => c.id)).toEqual(['a', 'stuck', 'b'])
  })
})

describe('sortWithinSection — §5.1.1 rule 1 (intra-bucket promotion)', () => {
  it('needs_you sorts first, then working, then everything else in its existing order', () => {
    const items = [conv('idle1', 1), conv('needsyou', 3), conv('working', 2), conv('idle2', 4)]
    const statusMap: Record<string, ConvRowStatus> = { needsyou: 'needs_you', working: 'working' }
    const statusFor = (id: string) => statusMap[id] ?? 'idle'

    expect(sortWithinSection(items, statusFor).map((c) => c.id)).toEqual([
      'needsyou',
      'working',
      'idle1',
      'idle2',
    ])
  })

  it('needs_you outranks working even when working is more recent', () => {
    const items = [conv('working', 1), conv('needsyou', 5)] // working is NEWER
    const statusMap: Record<string, ConvRowStatus> = { working: 'working', needsyou: 'needs_you' }
    const statusFor = (id: string) => statusMap[id] ?? 'idle'

    expect(sortWithinSection(items, statusFor).map((c) => c.id)).toEqual(['needsyou', 'working'])
  })

  it('a stable sort — ties keep their incoming (recency) order', () => {
    const items = [conv('newer', 1), conv('older', 2)]
    const statusFor = () => 'idle' as ConvRowStatus
    expect(sortWithinSection(items, statusFor).map((c) => c.id)).toEqual(['newer', 'older'])
  })

  it('does not mutate the input array', () => {
    const items = [conv('a', 1), conv('b', 2)]
    const copy = [...items]
    sortWithinSection(items, () => 'idle')
    expect(items).toEqual(copy)
  })
})
