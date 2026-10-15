import { describe, expect, it } from 'vitest'

// "Last updated" must mean last ACTIVITY, not last viewed. updateConversationMeta fires on
// every hydration — opening a conversation reports the count it already had — so stamping
// `now` unconditionally let a conversation you merely glanced at jump above one you had
// actually used, then drop back on reload because the server was never told.

/** The rule the hook applies, in isolation from React state plumbing. */
function nextUpdatedAt(
  stored: { messageCount: number; updatedAt: string },
  count: number,
  now: string,
) {
  return count > stored.messageCount ? now : stored.updatedAt
}

const NOW = '2026-08-03T09:00:00.000Z'
const EARLIER = '2026-08-01T09:00:00.000Z'

describe('updatedAt on a metadata update', () => {
  it('does not move when a conversation is merely opened', () => {
    expect(nextUpdatedAt({ messageCount: 12, updatedAt: EARLIER }, 12, NOW)).toBe(EARLIER)
  })

  it('moves when a turn adds messages', () => {
    expect(nextUpdatedAt({ messageCount: 12, updatedAt: EARLIER }, 14, NOW)).toBe(NOW)
  })

  it('does not move when a conversation somehow reports FEWER messages', () => {
    // A truncated cache or a mid-switch render must not be read as activity.
    expect(nextUpdatedAt({ messageCount: 12, updatedAt: EARLIER }, 3, NOW)).toBe(EARLIER)
  })

  it('moves on the first message of a brand-new conversation', () => {
    expect(nextUpdatedAt({ messageCount: 0, updatedAt: EARLIER }, 1, NOW)).toBe(NOW)
  })
})
