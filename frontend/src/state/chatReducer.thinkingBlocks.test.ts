// @vitest-environment jsdom
//
// Live reasoning arrives as a positioned BLOCK, not one growing string.
//
// A turn thinks, calls a tool, then thinks again. Those are two thoughts belonging either
// side of the tool row, and concatenating every `thinking_delta` into `Message.thinking` drew
// them merged above the first tool call — including the half that came after it.
//
// `thinking` is still accumulated alongside: it stays the message-level field older readers
// use, and the fallback for a transcript recorded before this existed.

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

async function run(owner: string, events: string[]) {
  chatSessions.apply(owner, (s) => ({
    ...s,
    messages: [assistantMsg(`${owner}-1`)],
    openAssistantId: `${owner}-1`,
  }))
  await consumeEventStream(owner, sseResponse(events), { suggestionsEnabled: false })
  return chatSessions.getSlice(owner).messages[0]!
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('thinking_delta becomes a positioned block', () => {
  it('grows ONE block while consecutive deltas keep arriving', async () => {
    const m = await run('think-a', [
      '{"type":"thinking_delta","text":"Let me "}',
      '{"type":"thinking_delta","text":"check."}',
    ])
    const thoughts = (m.blocks || []).filter((b) => b.type === 'thinking')
    expect(thoughts).toHaveLength(1)
    expect(thoughts[0]).toMatchObject({ content: 'Let me check.' })
  })

  it('opens a SECOND block when a tool call came between the thoughts', async () => {
    const m = await run('think-b', [
      '{"type":"thinking_delta","text":"First look."}',
      '{"type":"tool_use_start","id":"t1","name":"get_transactions"}',
      '{"type":"thinking_delta","text":"Now answer."}',
    ])
    expect((m.blocks || []).map((b) => b.type)).toEqual(['thinking', 'tool', 'thinking'])
    const thoughts = (m.blocks || []).filter((b) => b.type === 'thinking')
    expect(thoughts.map((b) => (b as { content: string }).content)).toEqual([
      'First look.',
      'Now answer.',
    ])
  })

  it('still accumulates the message-level field, for older readers', async () => {
    const m = await run('think-c', [
      '{"type":"thinking_delta","text":"AAA"}',
      '{"type":"tool_use_start","id":"t1","name":"get_transactions"}',
      '{"type":"thinking_delta","text":"BBB"}',
    ])
    expect(m.thinking).toBe('AAABBB')
  })
})
