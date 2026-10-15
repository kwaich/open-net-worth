// @vitest-environment jsdom
//
// Live report 2026-08-10: two stacked "Thinking" pills for one "how are you" greeting,
// followed by a single answer. Root cause: chatReducer.replayDedup.test.ts's dedup
// (armSkipFromReplay) only arms off a RECOGNIZED TOOL BLOCK — a pure smalltalk turn makes
// no tool calls, so a reconnect mid-stream (the interrupted first connection's bubble left
// with no `usage`/`error` and `openAssistantId` cleared, e.g. by a dev-server --reload
// restart racing the turn, or a page reload) had nothing to arm it. The second connection's
// full replay of thinking_delta/text_delta then opened a BRAND NEW bubble via
// ensureAssistant instead of continuing the interrupted one — two separate "Thinking"
// blocks where there should be one.
//
// Fix: chatReducer.ts's consumeEventStream now also arms the same skip-by-prefix-length
// dedup off the trailing message alone, whenever nothing (`openAssistantId`) is already
// pointing anywhere AND that message never reached a genuine terminal state (no `usage`,
// no `error`) — independent of whether any tool call is involved.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'
import type { Message } from '../types/chat'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

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

describe('replay dedup with NO tool call to anchor it', () => {
  it('a reconnect to an orphaned no-tool-call bubble continues it instead of opening a second one', async () => {
    // Simulates the interrupted first connection: it managed to write a PARTIAL thinking
    // block before dying (no turn_done, no error — a plain disconnect), leaving no usage
    // stamped and openAssistantId cleared (a fresh mount after a page reload, or nothing
    // ever re-pointed it).
    const seeded: Message = {
      id: 'a1',
      role: 'assistant',
      text: '',
      thinking: 'The user is asking "how are you"',
      streaming: false,
      toolCalls: [],
      blocks: [],
      proposedChanges: [],
    }
    chatSessions.apply('conv-smalltalk', (s) => ({
      ...s,
      status: 'streaming',
      messages: [seeded],
      openAssistantId: null,
    }))

    // The reconnect's replay: the FULL thinking (same prefix, continued) and the answer —
    // no tool call anywhere in this turn.
    await consumeEventStream(
      'conv-smalltalk',
      sseResponse([
        '{"type":"thinking_delta","text":"The user is asking \\"how are you\\" - this is a greeting."}',
        '{"type":"text_delta","text":"Doing great — what\'s on your mind?"}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('conv-smalltalk').messages
    expect(msgs.filter((m) => m.role === 'assistant')).toHaveLength(1)
    expect(msgs[0]?.thinking).toBe('The user is asking "how are you" - this is a greeting.')
    expect(msgs[0]?.text).toBe("Doing great — what's on your mind?")
  })

  it('a normal fresh turn with no pre-existing bubble is unaffected', async () => {
    chatSessions.apply('conv-fresh', (s) => ({
      ...s,
      messages: [{ id: 'u1', role: 'user', text: 'hello' }],
      openAssistantId: null,
    }))

    await consumeEventStream(
      'conv-fresh',
      sseResponse([
        '{"type":"thinking_delta","text":"Simple greeting."}',
        '{"type":"text_delta","text":"Hey! What can I do for you?"}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('conv-fresh').messages
    const assistantMsgs = msgs.filter((m) => m.role === 'assistant')
    expect(assistantMsgs).toHaveLength(1)
    expect(assistantMsgs[0]?.thinking).toBe('Simple greeting.')
    expect(assistantMsgs[0]?.text).toBe('Hey! What can I do for you?')
  })
})
