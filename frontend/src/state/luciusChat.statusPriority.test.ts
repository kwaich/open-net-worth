// @vitest-environment jsdom
//
// resolveRowStatus — the pure priority-ordering function behind LuciusChatValue.statusFor
// (concurrent-conversations-ui-spec.md §1.1/§1.2/§1.3). Exercised directly, without
// rendering React, so the ranking and the E7 reload-survival fallback are pinned exactly.

import { describe, it, expect } from 'vitest'
import { resolveRowStatus } from './luciusChat'
import type { RowStatusInputs } from './luciusChat'

function base(over: Partial<RowStatusInputs> = {}): RowStatusInputs {
  return {
    ownStatus: undefined,
    ownLastResult: undefined,
    working: false,
    isActive: false,
    ...over,
  }
}

describe('resolveRowStatus — priority order', () => {
  it('idle by default — no slice, no server record, not working', () => {
    expect(resolveRowStatus(base())).toBe('idle')
  })

  it("needs_you when this browser's own slice is awaiting_approval", () => {
    expect(resolveRowStatus(base({ ownStatus: 'awaiting_approval' }))).toBe('needs_you')
  })

  it('needs_you OUTRANKS working — the highest-value case in the whole ranking (E6)', () => {
    // A conversation can be BOTH: paused for approval AND still reported "working" by a
    // stale/overlapping live signal. needs_you must win.
    const status = resolveRowStatus(base({ ownStatus: 'awaiting_approval', working: true }))
    expect(status).toBe('needs_you')
  })

  it('needs_you from the DURABLE server record outranks working too, with no local slice', () => {
    const status = resolveRowStatus(
      base({ working: true, remoteLastTurnStatus: 'awaiting_approval' }),
    )
    expect(status).toBe('needs_you')
  })

  it('needs_you is NOT gated on isActive — not cleared merely by viewing (§1.1)', () => {
    expect(resolveRowStatus(base({ ownStatus: 'awaiting_approval', isActive: true }))).toBe(
      'needs_you',
    )
  })

  it('working when a turn is in flight and nothing outranks it', () => {
    expect(resolveRowStatus(base({ working: true }))).toBe('working')
  })

  it('working never derives from remote last_turn_status alone — only from the working flag', () => {
    // A live turn is signalled ONLY via `working` (streamingIds), never inferred from
    // last_turn_status — the backend deliberately ends the live episode on pause (E6 note).
    expect(resolveRowStatus(base({ remoteLastTurnStatus: 'ok', working: false }))).not.toBe(
      'working',
    )
  })

  it('failed when this browser watched its own turn error out', () => {
    expect(resolveRowStatus(base({ ownLastResult: 'error' }))).toBe('failed')
  })

  it('done when this browser watched its own turn finish, and it is not the viewed one', () => {
    expect(resolveRowStatus(base({ ownLastResult: 'ok', isActive: false }))).toBe('done')
  })

  it('an answer you are already looking at is not "done"', () => {
    expect(resolveRowStatus(base({ ownLastResult: 'ok', isActive: true }))).toBe('idle')
  })

  it('working outranks a done/failed local record from an EARLIER turn', () => {
    expect(resolveRowStatus(base({ ownLastResult: 'error', working: true }))).toBe('working')
  })
})

describe('resolveRowStatus — E7 durable fallback (reload survival)', () => {
  const T0 = '2026-08-06T09:00:00.000Z' // last viewed
  const AFTER = '2026-08-06T10:00:00.000Z' // conversation changed after that
  const BEFORE = '2026-08-06T08:00:00.000Z' // conversation changed before that

  it('surfaces a success that happened since the last view, with no local slice (fresh reload)', () => {
    const status = resolveRowStatus(
      base({
        remoteLastTurnStatus: 'ok',
        remoteUpdatedAt: AFTER,
        lastViewedAt: T0,
        isActive: false,
      }),
    )
    expect(status).toBe('done')
  })

  it('surfaces a failure the same way', () => {
    const status = resolveRowStatus(
      base({
        remoteLastTurnStatus: 'error',
        remoteUpdatedAt: AFTER,
        lastViewedAt: T0,
        isActive: false,
      }),
    )
    expect(status).toBe('failed')
  })

  it('does NOT re-flag a turn already seen since — updatedAt before lastViewedAt', () => {
    const status = resolveRowStatus(
      base({
        remoteLastTurnStatus: 'ok',
        remoteUpdatedAt: BEFORE,
        lastViewedAt: T0,
        isActive: false,
      }),
    )
    expect(status).toBe('idle')
  })

  it('never fired on the active conversation, even if technically "unseen" by the timestamp', () => {
    const status = resolveRowStatus(
      base({
        remoteLastTurnStatus: 'ok',
        remoteUpdatedAt: AFTER,
        lastViewedAt: T0,
        isActive: true,
      }),
    )
    expect(status).toBe('idle')
  })

  it("a cancelled turn (the user's own explicit Stop) never surfaces as failed or done", () => {
    const status = resolveRowStatus(
      base({
        remoteLastTurnStatus: 'cancelled',
        remoteUpdatedAt: AFTER,
        lastViewedAt: T0,
        isActive: false,
      }),
    )
    expect(status).toBe('idle')
  })

  it('the LOCAL slice always wins over the durable fallback when it has an opinion', () => {
    // This browser's own record says 'ok' and already cleared (isActive false, ownLastResult
    // 'ok') — the durable record disagreeing (a stale 'error' from before a retry) must not
    // override it.
    const status = resolveRowStatus(
      base({
        ownLastResult: 'ok',
        isActive: false,
        remoteLastTurnStatus: 'error',
        remoteUpdatedAt: AFTER,
        lastViewedAt: T0,
      }),
    )
    expect(status).toBe('done')
  })
})

describe('resolveRowStatus — read on another client', () => {
  const TURN_ENDED = '2026-10-03T09:00:00.000000+00:00' // server clock, Python spelling
  const READ_ELSEWHERE = '2026-10-03T09:05:00+00:00'
  const READ_HERE_LONG_AGO = '2026-10-02T09:00:00.000Z' // this browser's own, older stamp

  it('clears the marker when ANY client read the conversation after the turn ended', () => {
    const status = resolveRowStatus(
      base({
        remoteLastTurnStatus: 'ok',
        remoteLastTurnAt: TURN_ENDED,
        remoteLastReadAt: READ_ELSEWHERE,
        lastViewedAt: READ_HERE_LONG_AGO,
      }),
    )
    expect(status).toBe('idle')
  })

  it('a turn that ends AFTER the last read is unread again', () => {
    const status = resolveRowStatus(
      base({
        remoteLastTurnStatus: 'ok',
        remoteLastTurnAt: '2026-10-03T10:00:00+00:00',
        remoteLastReadAt: READ_ELSEWHERE,
      }),
    )
    expect(status).toBe('done')
  })

  it('judges "a reply arrived" by last_turn_at, not by updated_at', () => {
    // The person sent their OWN message after reading — updated_at moved, no reply landed.
    const status = resolveRowStatus(
      base({
        remoteLastTurnStatus: 'ok',
        remoteLastTurnAt: TURN_ENDED,
        remoteUpdatedAt: '2026-10-03T09:30:00+00:00',
        remoteLastReadAt: READ_ELSEWHERE,
      }),
    )
    expect(status).toBe('idle')
  })

  it('compares instants, not strings — a Python stamp without a fraction vs a browser stamp', () => {
    const status = resolveRowStatus(
      base({
        remoteLastTurnStatus: 'ok',
        remoteLastTurnAt: '2026-10-03T09:00:01+00:00',
        lastViewedAt: '2026-10-03T09:00:00.900Z',
      }),
    )
    // '+' sorts before '.', so as TEXT the turn looked older than the view and was hidden.
    expect(status).toBe('done')
  })

  it('a conversation nobody ever read, with a finished turn, is unread', () => {
    expect(
      resolveRowStatus(base({ remoteLastTurnStatus: 'ok', remoteLastTurnAt: TURN_ENDED })),
    ).toBe('done')
  })

  it("this browser's own 'done' yields to a server record that the turn was read", () => {
    const status = resolveRowStatus(
      base({
        ownLastResult: 'ok',
        remoteLastTurnStatus: 'ok',
        remoteLastTurnAt: TURN_ENDED,
        remoteLastReadAt: READ_ELSEWHERE,
      }),
    )
    expect(status).toBe('idle')
  })

  it("this browser's own 'failed' yields the same way, but never on the conversation you are viewing", () => {
    const read = {
      ownLastResult: 'error' as const,
      remoteLastTurnStatus: 'error' as const,
      remoteLastTurnAt: TURN_ENDED,
      remoteLastReadAt: READ_ELSEWHERE,
    }
    expect(resolveRowStatus(base({ ...read, isActive: false }))).toBe('idle')
    expect(resolveRowStatus(base({ ...read, isActive: true }))).toBe('failed')
  })

  it('a server record of a DIFFERENT outcome never hides what the local slice saw', () => {
    // The listing has not caught up: it still describes the earlier, read, successful turn.
    // Its read stamp says nothing about the failure this browser just watched happen.
    const status = resolveRowStatus(
      base({
        ownLastResult: 'error',
        isActive: false,
        remoteLastTurnStatus: 'ok',
        remoteLastTurnAt: TURN_ENDED,
        remoteLastReadAt: READ_ELSEWHERE,
      }),
    )
    expect(status).toBe('failed')
  })
})

// Owner report 2026-10-14: a failed Automation run said "Failed" only until it was read, and
// one that failed after a turn that said `ok` said "New reply". The run's own status is the
// truth, and it outlives being read.
describe('resolveRowStatus — a failed Automation run', () => {
  const ended = '2026-10-13T16:30:00Z'
  const read = '2026-10-14T03:00:00Z'

  it('stays "Failed" after it has been read — as run_failed, which is not unread', () => {
    const status = resolveRowStatus(
      base({
        runFailed: true,
        remoteLastTurnStatus: 'error',
        remoteLastTurnAt: ended,
        remoteLastReadAt: read,
      }),
    )
    expect(status).toBe('run_failed')
  })

  it('is the unread failure while nobody has read it', () => {
    const status = resolveRowStatus(
      base({ runFailed: true, remoteLastTurnStatus: 'error', remoteLastTurnAt: ended }),
    )
    expect(status).toBe('failed')
  })

  it('says Failed, not New reply, when the run failed after a turn that said ok', () => {
    const status = resolveRowStatus(
      base({ runFailed: true, remoteLastTurnStatus: 'ok', remoteLastTurnAt: ended }),
    )
    expect(status).toBe('failed')
  })

  it('shows Failed on the run you are looking at too', () => {
    expect(resolveRowStatus(base({ runFailed: true, isActive: true }))).toBe('run_failed')
  })

  it('yields to a retry in flight and to an approval', () => {
    expect(resolveRowStatus(base({ runFailed: true, working: true }))).toBe('working')
    expect(
      resolveRowStatus(base({ runFailed: true, remoteLastTurnStatus: 'awaiting_approval' })),
    ).toBe('needs_you')
  })
})
