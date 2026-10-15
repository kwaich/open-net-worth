// @vitest-environment jsdom
//
// Live report 2026-08-10: sent "hello", then "how are you?" before Lucius's first reply
// arrived, then "i hope you are doing fine" right after seeing that reply. The real session
// transcript confirmed the backend answered in perfect alternating order (Q1,A1,Q2,A2,Q3,A3)
// — but the client rendered Q1,Q2,A1,Q3,A2,A3, because a question typed ahead of its own
// answer always won the race for the array's trailing slot (see ConvSlice.queuedMessages).
//
// This reproduces that exact transcript, driven through the real reducer primitives
// (addUserMessage/addQueuedMessage/consumeEventStream), and asserts the array now matches
// the true, alternating order the backend actually produced.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { addUserMessage, addQueuedMessage, consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'

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

describe('a question typed ahead of its own answer no longer wins the array-position race', () => {
  it('reproduces the real hello / how-are-you / i-hope-you-are-doing-fine transcript order', async () => {
    const ownerId = 'conv-timeline'
    chatSessions.apply(ownerId, (s) => ({ ...s, status: 'streaming' }))

    addUserMessage(ownerId, { id: 'q1', role: 'user', text: 'hello' })
    // Q2 sent before Lucius's reply to Q1 has arrived.
    addQueuedMessage(ownerId, { id: 'q2', role: 'user', text: 'how are you?', pending: 'queued' })

    // A1 answers Q1 alone; the server then drains Q2 at the next checkpoint.
    await consumeEventStream(
      ownerId,
      sseResponse([
        '{"type":"text_delta","text":"Hey there! What can I do for you today?"}',
        '{"type":"message_delivered","message_id":"q2"}',
      ]),
      { suggestionsEnabled: false },
    )

    // Q3 sent while the server is now working on Q2's answer.
    addQueuedMessage(ownerId, {
      id: 'q3',
      role: 'user',
      text: 'i hope you are doing fine',
      pending: 'queued',
    })

    // A2 answers Q2; the server drains Q3; A3 answers Q3.
    await consumeEventStream(
      ownerId,
      sseResponse([
        '{"type":"text_delta","text":"Doing well, thanks for asking! What\'s on your mind?"}',
        '{"type":"message_delivered","message_id":"q3"}',
        '{"type":"text_delta","text":"That\'s kind of you to say — I\'m all good. What can I help with?"}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const slice = chatSessions.getSlice(ownerId)
    expect(slice.queuedMessages).toHaveLength(0)
    expect(slice.messages.map((m) => m.text)).toEqual([
      'hello',
      'Hey there! What can I do for you today?',
      'how are you?',
      "Doing well, thanks for asking! What's on your mind?",
      'i hope you are doing fine',
      "That's kind of you to say — I'm all good. What can I help with?",
    ])
    expect(slice.messages.every((m) => !m.pending)).toBe(true)
  })
})
