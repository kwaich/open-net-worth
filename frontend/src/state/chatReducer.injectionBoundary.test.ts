// @vitest-environment jsdom
//
// Live report 2026-08-10: "when i ask about comitments, it's ok / then, i also queued a
// riverside one / now, the response of the riverside one, is fucking appending into the
// above response, not after". Verified against the real session transcript: the injected
// follow-up rides the `«user_msg:...»` fence into the SAME still-running turn
// (assistant.py's mid-turn injection), so turn_done never fires between the two answers.
// rehydrate_messages already treats any real injected text as ending the prior bubble
// (continues_turn = False, unconditionally) — the live path had no equivalent signal, so
// ensureAssistant correctly found the still-`streaming` PRE-injection bubble (see its own
// reverse-scan fix) and kept writing the SECOND, unrelated answer into it.
//
// Fix: message_delivered — the event marking exactly the moment the injected message was
// spliced in — now also clears `streaming` on whatever bubble was open, so the very next
// token opens a fresh one instead of reusing the pre-injection bubble.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { consumeEventStream } from './chatReducer'
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

describe('a mid-turn injected message starts a genuinely fresh bubble for its own answer', () => {
  it('does not glue the injected reply onto the pre-injection answer', async () => {
    chatSessions.apply('conv-inject-boundary', (s) => ({
      ...s,
      status: 'streaming',
      messages: [{ id: 'u1', role: 'user', text: 'what commitments do i have?' }],
    }))

    // The commitments answer streams and finishes its own round...
    await consumeEventStream(
      'conv-inject-boundary',
      sseResponse([
        '{"type":"text_delta","text":"You have 5 active commitments: Parkview Residences..."}',
      ]),
      { suggestionsEnabled: false },
    )

    // ...the queued follow-up is spliced in — message_delivered names it, drained at the
    // natural checkpoint right after the round above completed.
    chatSessions.apply('conv-inject-boundary', (s) => ({
      ...s,
      messages: [
        ...s.messages,
        { id: 'u2', role: 'user', text: 'tell me about riverside one', pending: 'queued' },
      ],
    }))
    await consumeEventStream(
      'conv-inject-boundary',
      sseResponse(['{"type":"message_delivered","message_id":"u2"}']),
      { suggestionsEnabled: false },
    )

    // The turn continues with a NEW round answering the injected question.
    await consumeEventStream(
      'conv-inject-boundary',
      sseResponse([
        '{"type":"text_delta","text":"Total price: ~d5 billion, paid in 13 installments."}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('conv-inject-boundary').messages
    const assistants = msgs.filter((m) => m.role === 'assistant')
    expect(assistants).toHaveLength(2)
    expect(assistants[0]?.text).toBe('You have 5 active commitments: Parkview Residences...')
    expect(assistants[1]?.text).toBe('Total price: ~d5 billion, paid in 13 installments.')
    // True send order: commitments Q, commitments A, injected Q, riverside-one A.
    expect(msgs.map((m) => m.id)).toEqual(['u1', assistants[0]!.id, 'u2', assistants[1]!.id])
  })
})
