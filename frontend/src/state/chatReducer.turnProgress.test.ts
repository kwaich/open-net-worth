// @vitest-environment jsdom
//
// `turn_progress` — the running output-token total the working indicator shows.
//
// The number is the SERVER's accumulated figure, forwarded once per model round. It is
// deliberately not derived from streamed characters: an estimate rendered as "↓ 1.4k
// tokens" is a fabricated measurement, and the user has no way to tell it from a real one.
// These tests pin that it is carried through untouched and reset between turns.

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

describe('turn_progress → liveOutputTokens', () => {
  it('carries the server total through verbatim, taking the latest of several rounds', async () => {
    await consumeEventStream(
      'conv-tp',
      sseResponse([
        '{"type":"turn_progress","output_tokens":128}',
        '{"type":"text_delta","text":"partial"}',
        // A later round reports the ACCUMULATED total, not a per-round delta — so the last
        // value wins rather than being summed.
        '{"type":"turn_progress","output_tokens":1432}',
      ]),
      { suggestionsEnabled: false },
    )
    expect(chatSessions.getSlice('conv-tp').liveOutputTokens).toBe(1432)
  })

  it('ignores a malformed payload rather than showing NaN in the indicator', async () => {
    chatSessions.apply('conv-tp-bad', (s) => ({ ...s, liveOutputTokens: 55 }))
    await consumeEventStream('conv-tp-bad', sseResponse(['{"type":"turn_progress"}']), {
      suggestionsEnabled: false,
    })
    // No `output_tokens` ⇒ 0, never undefined/NaN reaching the formatter.
    expect(chatSessions.getSlice('conv-tp-bad').liveOutputTokens).toBe(0)
  })

  it('is refused for another conversation (INV-2), so one turn cannot move another count', async () => {
    chatSessions.apply('conv-tp-own', (s) => ({ ...s, liveOutputTokens: 7 }))
    await consumeEventStream(
      'conv-tp-own',
      sseResponse(['{"type":"turn_progress","output_tokens":999,"session_id":"someone-else"}']),
      { suggestionsEnabled: false },
    )
    expect(chatSessions.getSlice('conv-tp-own').liveOutputTokens).toBe(7)
  })
})
