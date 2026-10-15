// @vitest-environment jsdom
//
// Mid-turn injection (concurrent-conversations RFC, injection phase; acceptance C2): the
// `message_delivered` SSE event moves the NAMED message out of the queue and into the true
// timeline — and only that message, by id, never a positional guess. See
// ConvSlice.queuedMessages for why a queued message lives in its own list rather than as a
// `pending`-flagged entry inside `messages`: the array slot it earns is decided at DELIVERY
// time, not at send time.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'
import type { Message } from '../types/chat'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

function pendingUserMsg(id: string): Message {
  return { id, role: 'user', text: 'hi', pending: 'queued' }
}

function assistantMsg(id: string): Message {
  return {
    id,
    role: 'assistant',
    text: '',
    streaming: true,
    toolCalls: [],
    blocks: [],
    proposedChanges: [],
  }
}

function sseResponse(events: string[]): Response {
  const encoder = new TextEncoder()
  const body = events.map((e) => `data: ${e}\n\n`).join('') + 'data: [DONE]\n\n'
  return new Response(
    new ReadableStream({
      start(ctrl) {
        ctrl.enqueue(encoder.encode(body))
        ctrl.close()
      },
    }),
    { status: 200 },
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('consumeEventStream: message_delivered moves the named message into the true timeline', () => {
  it('moves the message whose id the event names out of the queue, pending cleared', async () => {
    chatSessions.apply('own-md-1', (s) => ({
      ...s,
      messages: [assistantMsg('a1')],
      queuedMessages: [pendingUserMsg('u1')],
    }))

    await consumeEventStream(
      'own-md-1',
      sseResponse(['{"type":"message_delivered","message_id":"u1"}']),
      { suggestionsEnabled: false },
    )

    const slice = chatSessions.getSlice('own-md-1')
    expect(slice.queuedMessages).toHaveLength(0)
    // Appended at the true end of the timeline — its correct position now that it is
    // actually being processed, not wherever it happened to sit at send time.
    const delivered = slice.messages.find((m) => m.id === 'u1')
    expect(delivered?.pending).toBeUndefined()
    expect(slice.messages.map((m) => m.id)).toEqual(['a1', 'u1'])
  })

  it("does not touch a DIFFERENT queued message's pending state", async () => {
    chatSessions.apply('own-md-2', (s) => ({
      ...s,
      queuedMessages: [pendingUserMsg('u1'), pendingUserMsg('u2')],
    }))

    await consumeEventStream(
      'own-md-2',
      sseResponse(['{"type":"message_delivered","message_id":"u1"}']),
      { suggestionsEnabled: false },
    )

    const slice = chatSessions.getSlice('own-md-2')
    expect(slice.messages.map((m) => m.id)).toEqual(['u1'])
    expect(slice.messages[0]?.pending).toBeUndefined()
    // u2 is still queued, still pending — untouched.
    expect(slice.queuedMessages.map((m) => m.id)).toEqual(['u2'])
    expect(slice.queuedMessages[0]?.pending).toBe('queued')
  })

  it('is a no-op when the named id is not queued — never throws, never touches other state', async () => {
    chatSessions.apply('own-md-3', (s) => ({ ...s, queuedMessages: [pendingUserMsg('u1')] }))

    await consumeEventStream(
      'own-md-3',
      sseResponse(['{"type":"message_delivered","message_id":"does-not-exist"}']),
      { suggestionsEnabled: false },
    )

    const slice = chatSessions.getSlice('own-md-3')
    expect(slice.messages).toHaveLength(0)
    expect(slice.queuedMessages.map((m) => m.id)).toEqual(['u1'])
    expect(slice.queuedMessages[0]?.pending).toBe('queued')
  })

  it('a message already delivered (already in `messages`, not queued) is left alone — idempotent', async () => {
    chatSessions.apply('own-md-4', (s) => ({
      ...s,
      messages: [{ id: 'u1', role: 'user', text: 'hi' }],
    }))

    await consumeEventStream(
      'own-md-4',
      sseResponse(['{"type":"message_delivered","message_id":"u1"}']),
      { suggestionsEnabled: false },
    )

    const slice = chatSessions.getSlice('own-md-4')
    expect(slice.messages).toHaveLength(1)
    expect(slice.messages[0]?.pending).toBeUndefined()
    expect(slice.queuedMessages).toHaveLength(0)
  })
})
