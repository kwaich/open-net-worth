// @vitest-environment jsdom
//
// REGRESSION (mobile incident, 2026-09-06): a staged `update_accounts` approval card was
// tapped ("Allow once"), but the server could not redeem the pending tool_use — a later
// message in the conversation had already replaced it — and streamed back an `error` event
// carrying the SAME `tool_use_id`. The reducer's `error` branch un-paints the optimistic tick:
// `status: undefined, refused: true` on the matching `ProposedChange`, both on the message's
// `proposedChanges` array AND its `blocks` (a card can be reached through either, depending on
// which the rendering surface reads). ChangePreview.tsx then renders that as a TERMINAL status
// row rather than re-arming the buttons (see ChangePreview.refused.test.tsx) — but this pins
// the REDUCER half of that contract: an `error` naming a `tool_use_id` must flip exactly this
// change to `refused: true` / `status: undefined`, on both places it lives, and must NOT touch
// any other change on the message.
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

describe('an error naming a tool_use_id refuses the matching approval, never another one', () => {
  it('sets refused: true and clears status on the change, both in proposedChanges and in blocks', async () => {
    chatSessions.apply('refuse-1', (s) => ({
      ...s,
      messages: [assistantMsg('a1')],
      openAssistantId: 'a1',
    }))

    await consumeEventStream(
      'refuse-1',
      sseResponse([
        '{"type":"proposed_change","tool_use_id":"tu-1","change":{"type":"txn_create","grant_tool":"add_transactions"}}',
        '{"type":"error","message":"This approval was already answered, or a later message in this conversation replaced it. Nothing was run — ask Lucius again.","tool_use_id":"tu-1"}',
      ]),
      { suggestionsEnabled: false },
    )

    const message = chatSessions.getSlice('refuse-1').messages[0]
    expect(message?.proposedChanges?.[0]).toMatchObject({
      id: 'tu-1',
      status: undefined,
      refused: true,
    })
    const changeBlock = message?.blocks?.find((b) => b.type === 'change')
    expect(changeBlock?.type === 'change' ? changeBlock.change : null).toMatchObject({
      id: 'tu-1',
      status: undefined,
      refused: true,
    })
  })

  it('leaves an UNRELATED change on the same message untouched', async () => {
    chatSessions.apply('refuse-2', (s) => ({
      ...s,
      messages: [assistantMsg('a1')],
      openAssistantId: 'a1',
    }))

    await consumeEventStream(
      'refuse-2',
      sseResponse([
        '{"type":"proposed_change","tool_use_id":"tu-other","change":{"type":"txn_create","grant_tool":"add_transactions"}}',
        '{"type":"proposed_change","tool_use_id":"tu-1","change":{"type":"txn_create","grant_tool":"add_transactions"}}',
        '{"type":"error","message":"refused","tool_use_id":"tu-1"}',
      ]),
      { suggestionsEnabled: false },
    )

    const changes = chatSessions.getSlice('refuse-2').messages[0]?.proposedChanges ?? []
    const other = changes.find((c) => c.id === 'tu-other')
    const refused = changes.find((c) => c.id === 'tu-1')
    expect(other?.refused).toBeFalsy()
    expect(refused?.refused).toBe(true)
  })
})
