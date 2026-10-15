// @vitest-environment jsdom
//
// Live report 2026-08-10: an answer stopped mid-word ("It's separ") on the live stream
// and only showed the full text ("It's separate from the broader Uber-ONWAI deal...")
// after a manual page reload. The backend had the whole answer all along — the client
// silently threw the tail away.
//
// Root cause: the old replay-dedup used a sticky `replaying` boolean, set true the
// instant a replayed (already-known) tool_use_start was recognized and cleared ONLY by
// a later FRESH tool_use_start. A turn whose replayed tail led straight into brand-new
// text_delta with no further tool call in between never cleared the flag, so every
// genuinely new token after the replay was suppressed for the rest of the connection.
//
// Fix: dedup text/thinking by PREFIX LENGTH, armed once off the first recognized replay,
// so the skip counter naturally runs out and normal appending resumes on its own —
// independent of whether another tool call ever follows.

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

describe('a replayed tool call whose tail is brand-new text is not truncated', () => {
  it('resumes appending once the replayed prefix is consumed, with no further tool call', async () => {
    const seeded: Message = {
      id: 'a1',
      role: 'assistant',
      text: "It's separ", // torn-down connection got this far before reconnecting
      streaming: false,
      toolCalls: ['get_holding_detail'],
      blocks: [
        { type: 'tool', id: 'tu-1', name: 'get_holding_detail', status: 'done', result: 'r1' },
      ],
      proposedChanges: [],
    }
    chatSessions.apply('conv-resume-text', (s) => ({
      ...s,
      status: 'streaming',
      messages: [seeded],
    }))

    await consumeEventStream(
      'conv-resume-text',
      sseResponse([
        // Replay of the already-seen tool call, then a replay of the SAME prefix text
        // already rendered, then the genuinely new tail — no further tool call at all.
        '{"type":"tool_use_start","id":"tu-1","name":"get_holding_detail"}',
        '{"type":"tool_done","id":"tu-1","result":"r1"}',
        '{"type":"text_delta","text":"It\'s separ"}',
        '{"type":"text_delta","text":"ate from the broader Uber-ONWAI deal — this is just the Taiwan carve-out."}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('conv-resume-text').messages
    expect(msgs.filter((m) => m.role === 'assistant')).toHaveLength(1)
    expect(msgs[0]?.text).toBe(
      "It's separate from the broader Uber-ONWAI deal — this is just the Taiwan carve-out.",
    )
  })

  it('a resumed permission-card continuation (no replayed tool block) is never skipped', async () => {
    // resolvePendingToolUse leaves `streaming: true` with pre-pause text already on the
    // message, explicitly re-opens the pointer (chatReducer.openAssistant), AND tells this
    // connection it is a deliberate resume via `isResume` — that explicit signal, not
    // `openAssistantId` merely being set, is what must keep this from being treated as a
    // replay (see chatReducer.ts's own comment on why the pointer alone cannot tell this
    // apart from a reconnect that supersedes another still-open attach).
    const seeded: Message = {
      id: 'a1',
      role: 'assistant',
      text: 'Let me delete that for you.',
      streaming: true,
      toolCalls: [],
      blocks: [],
      proposedChanges: [{ id: 'toolu_1', type: 'tool_permission', status: 'confirmed' }],
    }
    chatSessions.apply('conv-resume-approval', (s) => ({
      ...s,
      status: 'streaming',
      messages: [seeded],
      openAssistantId: 'a1',
    }))

    await consumeEventStream(
      'conv-resume-approval',
      sseResponse([
        '{"type":"text_delta","text":"Deleted the duplicate transaction."}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false, isResume: true },
    )

    const msgs = chatSessions.getSlice('conv-resume-approval').messages
    expect(msgs[0]?.text).toBe('Let me delete that for you.Deleted the duplicate transaction.')
  })
})
