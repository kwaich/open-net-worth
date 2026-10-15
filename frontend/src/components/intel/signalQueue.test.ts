import { describe, expect, it } from 'vitest'
import { classifySignal, isFreshSignal } from './signalQueue'

const NOW = Date.parse('2026-09-24T12:00:00Z')

describe('signal queue', () => {
  it('keeps a fresh actionable alert in the decision queue', () => {
    expect(
      classifySignal(
        {
          status: 'action_needed',
          action_items: ['Review position'],
          updated_at: '2026-09-24T10:00:00Z',
        },
        NOW,
      ),
    ).toBe('action_needed')
  })

  it('does not call a no-action item a decision', () => {
    expect(
      classifySignal(
        {
          status: 'action_needed',
          action_items: ['No action needed'],
          updated_at: '2026-09-24T10:00:00Z',
        },
        NOW,
      ),
    ).toBe('monitoring')
  })

  it('puts old decisions into a recheck queue without discarding them', () => {
    expect(
      classifySignal(
        {
          status: 'action_needed',
          action_items: ['Review position'],
          updated_at: '2026-09-01T10:00:00Z',
        },
        NOW,
      ),
    ).toBe('needs_recheck')
  })

  it('does not label an old opportunity as new', () => {
    expect(isFreshSignal({ updated_at: '2026-09-01T10:00:00Z' }, NOW)).toBe(false)
  })

  it('keeps a watch story with no action items as a decision (its flags are the decision)', () => {
    expect(
      classifySignal(
        { status: 'action_needed', action_items: [], updated_at: '2026-09-24T10:00:00Z' },
        NOW,
      ),
    ).toBe('action_needed')
    expect(
      classifySignal({ status: 'action_needed', updated_at: '2026-09-24T10:00:00Z' } as never, NOW),
    ).toBe('action_needed')
  })
})
