// @vitest-environment jsdom
//
// An external-MCP tool's captured image (a screenshot, e.g.) rides the SAME `m.attachments`
// row a chat photo upload uses (MessageBubble.tsx's AttachmentThumb) — never a new block
// type. The `mcp_image` SSE event just appends into that array.

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

describe('mcp_image event appends to m.attachments', () => {
  it('appends the captured image onto the open assistant message', async () => {
    chatSessions.apply('own-a', (s) => ({
      ...s,
      messages: [assistantMsg('a1')],
      openAssistantId: 'a1',
    }))
    await consumeEventStream(
      'own-a',
      sseResponse([
        JSON.stringify({
          type: 'mcp_image',
          tool_use_id: 'tu-1',
          attachments: [{ ref_id: 'att-1', kind: 'image', summary: 'Screenshot' }],
        }),
      ]),
      { suggestionsEnabled: false },
    )
    const msg = chatSessions.getSlice('own-a').messages[0]
    expect(msg?.attachments).toEqual([{ ref_id: 'att-1', kind: 'image', summary: 'Screenshot' }])
  })

  it('appends onto EXISTING attachments rather than replacing them', async () => {
    chatSessions.apply('own-b', (s) => ({
      ...s,
      messages: [
        {
          ...assistantMsg('b1'),
          attachments: [{ ref_id: 'att-0', kind: 'image', summary: 'First' }],
        },
      ],
      openAssistantId: 'b1',
    }))
    await consumeEventStream(
      'own-b',
      sseResponse([
        JSON.stringify({
          type: 'mcp_image',
          tool_use_id: 'tu-1',
          attachments: [{ ref_id: 'att-1', kind: 'image', summary: 'Second' }],
        }),
      ]),
      { suggestionsEnabled: false },
    )
    const msg = chatSessions.getSlice('own-b').messages[0]
    expect(msg?.attachments?.map((a) => a.ref_id)).toEqual(['att-0', 'att-1'])
  })
})
