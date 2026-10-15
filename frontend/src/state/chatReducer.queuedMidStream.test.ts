// @vitest-environment jsdom
//
// Live report 2026-08-10: sent "and alloy?" while the ORCL answer was still streaming.
// The ORCL answer split mid-sentence — its FIRST half stayed on the original bubble
// (before the queued "and alloy?"), its SECOND half rendered as a brand-new bubble
// AFTER "and alloy?", reading as if it were alloy's answer. Also reported as "queued
// message is moved and cut off the stream after it's unqueued".
//
// Root cause: ensureAssistant checked only the LITERAL TRAILING array element
// (`prev[prev.length - 1]`). addUserMessage appends a queued send after the currently
// streaming bubble (true send order) without touching that bubble at all — so once the
// queued message lands, the streaming bubble is no longer trailing. The turn's very next
// token then finds "no live bubble at the end" and opens a SECOND one after the queue,
// even though updateLastAssistant (a reverse scan for role, not position) would have
// found and correctly updated the original bubble on its own. Fixed by making
// ensureAssistant use the same reverse-scan-for-role check as updateLastAssistant.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { consumeEventStream, addUserMessage } from './chatReducer'
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

describe('a message queued mid-stream does not split the in-progress answer', () => {
  it('keeps the whole answer on ONE bubble even though a queued message landed after it', async () => {
    chatSessions.apply('conv-queue-split', (s) => ({
      ...s,
      status: 'streaming',
      messages: [{ id: 'u1', role: 'user', text: 'any news about orcle?' }],
    }))

    // The ORCL turn starts streaming...
    await consumeEventStream(
      'conv-queue-split',
      // A single stream can't straddle a mid-stream send, so simulate the SAME shape the
      // real reducer sees: the reducer's own primitives called in the order the live
      // stream + a queued send would actually interleave them.
      sseResponse([
        '{"type":"text_delta","text":"Mixed picture for ORCL, and quite different from P"}',
      ]),
      { suggestionsEnabled: false },
    )

    // ...then the user queues a second message while it's still mid-flight.
    addUserMessage('conv-queue-split', {
      id: 'u2',
      role: 'user',
      text: 'and alloy?',
      pending: 'queued',
    })

    // The ORCL stream continues — this is the exact next event after the queue landed.
    await consumeEventStream(
      'conv-queue-split',
      sseResponse([
        '{"type":"text_delta","text":"LTR/MSFT: record Q4 FY26 results in June."}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('conv-queue-split').messages
    const assistants = msgs.filter((m) => m.role === 'assistant')
    expect(assistants).toHaveLength(1)
    expect(assistants[0]?.text).toBe(
      'Mixed picture for ORCL, and quite different from PLTR/MSFT: record Q4 FY26 results in June.',
    )
    // And it renders BEFORE the queued question, not after — true send order preserved.
    const order = msgs.map((m) => m.id)
    expect(order).toEqual(['u1', assistants[0]!.id, 'u2'])
  })
})
