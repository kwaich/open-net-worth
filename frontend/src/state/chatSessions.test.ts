// Slice isolation — the core guarantee chatSessions.ts exists to provide (RFC
// .claude/rfcs/concurrent-conversations.md Phase 1). Every test uses ids unique to itself:
// the store is a module-level singleton and vitest does not reset modules between tests in
// the same file, so a shared id across tests would let one test's writes leak into another.

import { describe, it, expect, vi } from 'vitest'
import { chatSessions } from './chatSessions'
import type { Message } from '../types/chat'

function userMsg(id: string): Message {
  return { id, role: 'user', text: id, attachments: [] }
}

describe('chatSessions: per-slice isolation', () => {
  it('interleaved applies to two different ids never cross-write', () => {
    chatSessions.apply('iso-a', (s) => ({ ...s, messages: [...s.messages, userMsg('a1')] }))
    chatSessions.apply('iso-b', (s) => ({ ...s, messages: [...s.messages, userMsg('b1')] }))
    chatSessions.apply('iso-a', (s) => ({ ...s, messages: [...s.messages, userMsg('a2')] }))
    chatSessions.apply('iso-b', (s) => ({ ...s, messages: [...s.messages, userMsg('b2')] }))

    expect(chatSessions.getSlice('iso-a').messages.map((m) => m.id)).toEqual(['a1', 'a2'])
    expect(chatSessions.getSlice('iso-b').messages.map((m) => m.id)).toEqual(['b1', 'b2'])
  })

  it('subscribe(id) fires only for writes to THAT id — a write to A never wakes a B subscriber', () => {
    const cbA = vi.fn()
    const cbB = vi.fn()
    const unsubA = chatSessions.subscribe('sub-a', cbA)
    const unsubB = chatSessions.subscribe('sub-b', cbB)

    chatSessions.apply('sub-a', (s) => ({ ...s, messages: [userMsg('x')] }))

    expect(cbA).toHaveBeenCalledTimes(1)
    expect(cbB).not.toHaveBeenCalled()

    unsubA()
    unsubB()
  })

  it('apply is a no-op — no notify — when the updater returns the SAME reference', () => {
    const cb = vi.fn()
    const unsub = chatSessions.subscribe('noop-slice', cb)
    chatSessions.apply('noop-slice', (s) => s)
    expect(cb).not.toHaveBeenCalled()
    unsub()
  })

  it('getSlice returns a STABLE reference across calls until the next real apply', () => {
    const first = chatSessions.getSlice('stable-slice')
    const second = chatSessions.getSlice('stable-slice')
    expect(second).toBe(first)
    chatSessions.apply('stable-slice', (s) => ({ ...s, hydrated: true }))
    expect(chatSessions.getSlice('stable-slice')).not.toBe(first)
  })

  it('getStreamingIdsSnapshot includes only slices whose status is "streaming"', () => {
    chatSessions.apply('stream-a', (s) => ({ ...s, status: 'streaming' }))
    chatSessions.apply('stream-b', (s) => ({ ...s, status: 'awaiting_approval' }))
    chatSessions.apply('stream-c', (s) => ({ ...s, status: 'error' }))

    const ids = chatSessions.getStreamingIdsSnapshot()
    expect(ids).toContain('stream-a')
    expect(ids).not.toContain('stream-b')
    expect(ids).not.toContain('stream-c')
  })

  it('getStreamingIdsSnapshot keeps a stable reference while the streaming SET is unchanged', () => {
    chatSessions.apply('stream-snap', (s) => ({ ...s, status: 'streaming' }))
    const first = chatSessions.getStreamingIdsSnapshot()
    // A message-only change (no status transition) must not perturb the snapshot identity —
    // this is what lets useSyncExternalStore(subscribeAll, …) bail without re-rendering.
    chatSessions.apply('stream-snap', (s) => ({ ...s, messages: [userMsg('tok')] }))
    expect(chatSessions.getStreamingIdsSnapshot()).toBe(first)
  })

  it('subscribeAll fires on a status transition but not on a message-only change', () => {
    const cb = vi.fn()
    const unsub = chatSessions.subscribeAll(cb)
    chatSessions.apply('global-sub', (s) => ({ ...s, messages: [userMsg('tok')] }))
    expect(cb).not.toHaveBeenCalled()
    chatSessions.apply('global-sub', (s) => ({ ...s, status: 'streaming' }))
    expect(cb).toHaveBeenCalledTimes(1)
    unsub()
  })

  it('peekSlice does not create an entry; getSlice does', () => {
    expect(chatSessions.peekSlice('never-touched')).toBeUndefined()
    chatSessions.getSlice('touched-now')
    expect(chatSessions.peekSlice('touched-now')).toBeDefined()
  })

  it('statusFor defaults to "idle" for an id with no slice yet', () => {
    expect(chatSessions.statusFor('no-slice-at-all')).toBe('idle')
  })
})
