// @vitest-environment jsdom
//
// INV-2 (RFC .claude/rfcs/concurrent-conversations.md; ADR
// docs/adr/2026-08-06-a-turn-belongs-to-its-conversation-not-to-the-view.md): the reducer
// refuses — and logs — any event whose own session_id disagrees with the ownerId it was
// invoked with. Acceptance A2.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'
import type { Message } from '../types/chat'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

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

describe('consumeEventStream: INV-2 ownership check', () => {
  it('applies an event that carries no session_id — the common Phase-1 case', async () => {
    chatSessions.apply('own-a', (s) => ({
      ...s,
      messages: [assistantMsg('a1')],
      openAssistantId: 'a1',
    }))
    await consumeEventStream('own-a', sseResponse(['{"type":"text_delta","text":"hello"}']), {
      suggestionsEnabled: false,
    })
    expect(chatSessions.getSlice('own-a').messages[0]?.text).toBe('hello')
  })

  it('applies an event whose session_id MATCHES the ownerId', async () => {
    chatSessions.apply('own-b', (s) => ({
      ...s,
      messages: [assistantMsg('b1')],
      openAssistantId: 'b1',
    }))
    await consumeEventStream(
      'own-b',
      sseResponse(['{"type":"text_delta","text":"hi","session_id":"own-b"}']),
      { suggestionsEnabled: false },
    )
    expect(chatSessions.getSlice('own-b').messages[0]?.text).toBe('hi')
  })

  it('REFUSES and LOGS an event whose session_id does not match the ownerId — never applies it', async () => {
    chatSessions.apply('own-c', (s) => ({
      ...s,
      messages: [assistantMsg('c1')],
      openAssistantId: 'c1',
    }))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    await consumeEventStream(
      'own-c',
      sseResponse(['{"type":"text_delta","text":"should never land","session_id":"own-OTHER"}']),
      { suggestionsEnabled: false },
    )

    // Refused, not silently dropped: it was LOGGED.
    expect(warn).toHaveBeenCalledTimes(1)
    expect(warn.mock.calls[0]?.[0]).toContain('[chatReducer]')
    // And never applied to the slice it was routed to.
    expect(chatSessions.getSlice('own-c').messages[0]?.text).toBe('')
    // Nor did it leak into the slice the mismatched id named.
    expect(chatSessions.peekSlice('own-OTHER')).toBeUndefined()

    warn.mockRestore()
  })

  it('a mismatched event does not stop the rest of the stream from applying', async () => {
    chatSessions.apply('own-d', (s) => ({
      ...s,
      messages: [assistantMsg('d1')],
      openAssistantId: 'd1',
    }))
    vi.spyOn(console, 'warn').mockImplementation(() => {})

    await consumeEventStream(
      'own-d',
      sseResponse([
        '{"type":"text_delta","text":"wrong","session_id":"own-OTHER"}',
        '{"type":"text_delta","text":"right"}',
      ]),
      { suggestionsEnabled: false },
    )

    expect(chatSessions.getSlice('own-d').messages[0]?.text).toBe('right')
    vi.restoreAllMocks()
  })
})
