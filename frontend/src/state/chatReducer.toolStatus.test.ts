// @vitest-environment jsdom
//
// Live incident 2026-08-10: a tool chip stayed stuck showing the "busy" spinner forever
// even though its result had already arrived and the turn went on to finish. Root cause:
// `tool_done` only ever wrote `result` onto the matching block — the ONLY place that ever
// set `status: 'done'` was a bulk sweep inside `turn_done` that marks every tool block on
// the LAST assistant message done. If that block ever isn't on the message `turn_done`
// sweeps (whatever the exact reason), it never gets marked done — there was no other path
// back to "not busy". Fix: `tool_done` sets `status: 'done'` directly, the moment the
// result it corresponds to actually arrives, independent of whatever happens later.

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

describe('tool_done marks its OWN block done immediately', () => {
  it('sets status to done the moment tool_done arrives, before turn_done ever fires', async () => {
    chatSessions.apply('tool-1', (s) => ({
      ...s,
      messages: [assistantMsg('a1')],
      openAssistantId: 'a1',
    }))

    await consumeEventStream(
      'tool-1',
      sseResponse([
        '{"type":"tool_use_start","id":"tu-1","name":"get_transactions"}',
        '{"type":"tool_executing","id":"tu-1","input":{}}',
        '{"type":"tool_done","id":"tu-1","result":"{\\"ok\\":true}"}',
        // Deliberately no turn_done in this stream — the block must already read
        // "done" without it, since the whole point is not depending on that sweep.
      ]),
      { suggestionsEnabled: false },
    )

    const block = chatSessions.getSlice('tool-1').messages[0]?.blocks?.[0]
    expect(block).toMatchObject({ type: 'tool', id: 'tu-1', status: 'done', result: '{"ok":true}' })
  })

  it('an EARLIER tool round stays done even if a later assistant message becomes "last"', async () => {
    // Reproduces the actual stuck-forever shape: two tool rounds land on the SAME
    // message (the live path always reuses one bubble per turn), but this pins the
    // regression at the unit the bug actually lives in — tool_done must not depend on
    // which message is "last" by the time anything else happens.
    chatSessions.apply('tool-2', (s) => ({
      ...s,
      messages: [assistantMsg('a1')],
      openAssistantId: 'a1',
    }))

    await consumeEventStream(
      'tool-2',
      sseResponse([
        '{"type":"tool_use_start","id":"tu-1","name":"get_transactions"}',
        '{"type":"tool_done","id":"tu-1","result":"first"}',
        '{"type":"tool_use_start","id":"tu-2","name":"get_transactions"}',
        '{"type":"tool_done","id":"tu-2","result":"second"}',
      ]),
      { suggestionsEnabled: false },
    )

    const blocks = chatSessions.getSlice('tool-2').messages[0]?.blocks ?? []
    const first = blocks.find((b) => b.type === 'tool' && b.id === 'tu-1')
    const second = blocks.find((b) => b.type === 'tool' && b.id === 'tu-2')
    expect(first).toMatchObject({ status: 'done', result: 'first' })
    expect(second).toMatchObject({ status: 'done', result: 'second' })
  })

  it('turn_done still marks done any block that somehow never got its own tool_done (backstop)', async () => {
    chatSessions.apply('tool-3', (s) => ({
      ...s,
      messages: [assistantMsg('a1')],
      openAssistantId: 'a1',
    }))

    await consumeEventStream(
      'tool-3',
      sseResponse([
        '{"type":"tool_use_start","id":"tu-1","name":"get_transactions"}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const block = chatSessions.getSlice('tool-3').messages[0]?.blocks?.[0]
    expect(block).toMatchObject({ status: 'done' })
  })
})
