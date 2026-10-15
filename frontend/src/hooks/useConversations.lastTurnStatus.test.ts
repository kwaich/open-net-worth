import { describe, expect, it } from 'vitest'
import { lastTurnStatusOf } from './useConversations'

// A turn the watchdog cut off or a restart broke failed as surely as one that raised. Left
// unmapped, 4 of the owner's 10 failed runs never showed "Failed" at all (2026-10-14).
describe('lastTurnStatusOf', () => {
  it('reads a timed-out or interrupted turn as an error', () => {
    expect(lastTurnStatusOf('timeout')).toBe('error')
    expect(lastTurnStatusOf('interrupted')).toBe('error')
  })

  it('keeps the statuses a row already understood', () => {
    for (const s of ['ok', 'error', 'awaiting_approval', 'cancelled'] as const) {
      expect(lastTurnStatusOf(s)).toBe(s)
    }
  })

  it('drops anything else, including a turn still running', () => {
    expect(lastTurnStatusOf('running')).toBeUndefined()
    expect(lastTurnStatusOf(undefined)).toBeUndefined()
  })
})
