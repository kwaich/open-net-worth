// @vitest-environment jsdom
//
// "why are we not storing the timestamp of the messages? we need to store? and the
// timestamp of the queued message should be when its get processed" — a message's `ts`
// is stamped at the moment it enters the TRUE timeline, never at send time for a queued
// one. "in case users have more than 1 queue, the other ones must be a bit slower, like
// 1ms+" — two deliveries in the same tick still get strictly increasing timestamps.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { addUserMessage, addQueuedMessage, deliverQueuedMessage } from './chatReducer'
import { chatSessions } from './chatSessions'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('a message is stamped when it enters the true timeline, not when it is sent', () => {
  it('a direct (not busy) send is stamped immediately', () => {
    const ownerId = 'conv-ts-1'
    addUserMessage(ownerId, { id: 'u1', role: 'user', text: 'hi' })
    expect(chatSessions.getSlice(ownerId).messages[0]?.ts).toBeTypeOf('number')
  })

  it('a queued message carries NO timestamp until it is actually delivered', () => {
    const ownerId = 'conv-ts-2'
    addQueuedMessage(ownerId, { id: 'q1', role: 'user', text: 'queued', pending: 'queued' })
    expect(chatSessions.getSlice(ownerId).queuedMessages[0]?.ts).toBeUndefined()

    deliverQueuedMessage(ownerId, 'q1')
    const delivered = chatSessions.getSlice(ownerId).messages[0]
    expect(delivered?.ts).toBeTypeOf('number')
  })

  it('two messages delivered back to back are still strictly increasing, never tied', () => {
    const ownerId = 'conv-ts-3'
    addQueuedMessage(ownerId, { id: 'q1', role: 'user', text: 'first', pending: 'queued' })
    addQueuedMessage(ownerId, { id: 'q2', role: 'user', text: 'second', pending: 'queued' })

    // Both delivered in the same synchronous stretch — no real time passes between them,
    // mirroring the live report: several queued messages drained at one checkpoint.
    deliverQueuedMessage(ownerId, 'q1')
    deliverQueuedMessage(ownerId, 'q2')

    const [m1, m2] = chatSessions.getSlice(ownerId).messages
    expect(m1?.ts).toBeTypeOf('number')
    expect(m2?.ts).toBeTypeOf('number')
    expect(m2!.ts!).toBeGreaterThan(m1!.ts!)
  })
})
