// @vitest-environment jsdom
//
// Live incident 2026-08-10: reconnecting mid-turn (useChat's attachGen forcing a fresh
// GET /live when a message gets queued into an already-streaming turn) gets a REPLAY of
// everything the live-session buffer has published for this turn so far. Confirmed
// against a real session transcript (backend history) that the backend called each tool
// EXACTLY ONCE — the UI rendered the tool calls and the answer TWICE. Root cause: nothing
// in the reducer was idempotent against a replayed event — a repeated tool_use_start
// pushed a duplicate block, and once the bubble's own `streaming` flag had already been
// cleared, a repeated text_delta opened a whole SECOND, separate "finished" bubble.
//
// Fix: dedup tool_use_start by id (ids are minted server-side, never reused — an exact,
// safe key), suppress text_delta while replaying a known id, and route tool_executing/
// tool_done to wherever their block actually lives rather than only "the last message".

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'
import type { Message } from '../types/chat'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

function assistantMsg(id: string, streaming = true): Message {
  return {
    id,
    role: 'assistant',
    text: '',
    streaming,
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

describe('replay dedup: a reconnect that re-delivers already-seen events', () => {
  it('does not duplicate the bubble even when the existing one already reads streaming:false', async () => {
    // Reproduces the confirmed real shape: the FIRST connection already fully rendered
    // one tool call + the answer text, but its bubble's streaming flag was already
    // cleared (e.g. by the torn-down connection's own cleanup) by the time the SECOND
    // connection's replay arrives.
    chatSessions.apply('dup-1', (s) => ({
      ...s,
      status: 'streaming',
      messages: [
        {
          ...assistantMsg('a1', false),
          text: 'July spending summary: SGD 21,583',
          blocks: [
            {
              type: 'tool',
              id: 'tu-1',
              name: 'get_spending_summary',
              status: 'done',
              result: 'r1',
            },
          ],
        },
      ],
    }))

    // The reconnect's replay: the SAME tool call again, then the SAME answer text again.
    await consumeEventStream(
      'dup-1',
      sseResponse([
        '{"type":"tool_use_start","id":"tu-1","name":"get_spending_summary"}',
        '{"type":"tool_done","id":"tu-1","result":"r1"}',
        '{"type":"text_delta","text":"July spending summary: SGD 21,583"}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('dup-1').messages
    expect(msgs.filter((m) => m.role === 'assistant')).toHaveLength(1)
    expect(msgs[0]?.blocks).toHaveLength(1)
    // Text is NOT doubled either — the replayed delta was suppressed, not re-appended.
    expect(msgs[0]?.text).toBe('July spending summary: SGD 21,583')
  })

  it('a GENUINELY new tool call after the replay is NOT suppressed', async () => {
    chatSessions.apply('dup-2', (s) => ({
      ...s,
      status: 'streaming',
      messages: [
        {
          ...assistantMsg('a1', false),
          blocks: [
            { type: 'tool', id: 'tu-1', name: 'get_transactions', status: 'done', result: 'r1' },
          ],
        },
      ],
    }))

    await consumeEventStream(
      'dup-2',
      sseResponse([
        // Replay of the already-seen call...
        '{"type":"tool_use_start","id":"tu-1","name":"get_transactions"}',
        '{"type":"tool_done","id":"tu-1","result":"r1"}',
        // ...followed by a GENUINELY new one the stream continues with live.
        '{"type":"tool_use_start","id":"tu-2","name":"get_spending_summary"}',
        '{"type":"tool_done","id":"tu-2","result":"r2"}',
        '{"type":"text_delta","text":"a brand new answer"}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('dup-2').messages
    const assistantMsgs = msgs.filter((m) => m.role === 'assistant')
    expect(assistantMsgs).toHaveLength(1)
    const toolIds = (assistantMsgs[0]?.blocks ?? [])
      .filter((b) => b.type === 'tool')
      .map((b) => (b as { id: string }).id)
    expect(toolIds).toEqual(['tu-1', 'tu-2']) // no duplicate of tu-1, tu-2 present exactly once
    expect(assistantMsgs[0]?.text).toBe('a brand new answer')
  })

  it('a normal, non-replayed stream is completely unaffected', async () => {
    chatSessions.apply('normal-1', (s) => ({
      ...s,
      messages: [assistantMsg('a1')],
      openAssistantId: 'a1',
    }))

    await consumeEventStream(
      'normal-1',
      sseResponse([
        '{"type":"tool_use_start","id":"tu-1","name":"get_transactions"}',
        '{"type":"tool_done","id":"tu-1","result":"r1"}',
        '{"type":"text_delta","text":"the answer"}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('normal-1').messages
    expect(msgs.filter((m) => m.role === 'assistant')).toHaveLength(1)
    expect(msgs[0]?.text).toBe('the answer')
    // One tool block + one text block — both real, neither a duplicate.
    expect(msgs[0]?.blocks).toHaveLength(2)
  })

  it('a reconnect that supersedes another still-open attach dedupes even with no tool call', async () => {
    // Live report: a fresh conversation's answer rendered as a truncated copy immediately
    // followed by the complete one. Root cause — a turn with NO tool call has nothing for
    // the id-based dedup above to key off, so it falls to the "no tool block armed" prefix-
    // length fallback; that fallback used to gate on `openAssistantId` being UNSET, but a
    // reconnect that supersedes an earlier, still-open attach to the SAME live turn
    // deliberately leaves `openAssistantId` set (for continuity) — so the fallback never
    // armed, and the replayed thinking+text landed as brand-new duplicate blocks. Simulates
    // exactly that: a message mid-stream (no usage yet), pointer still set from the
    // superseded connection, `isResume` NOT set (this is an ordinary reconnect, not a
    // deliberate resume).
    chatSessions.apply('super-1', (s) => ({
      ...s,
      status: 'streaming',
      messages: [
        {
          id: 'a1',
          role: 'assistant',
          text: 'A few practical layers, from easiest to most hands',
          thinking: 'Since their book is empty, I should mention connecting accounts first.',
          streaming: true,
          toolCalls: [],
          blocks: [
            {
              type: 'thinking',
              content: 'Since their book is empty, I should mention connecting accounts first.',
            },
            { type: 'text', content: 'A few practical layers, from easiest to most hands' },
          ],
          proposedChanges: [],
        },
      ],
      openAssistantId: 'a1',
    }))

    await consumeEventStream(
      'super-1',
      sseResponse([
        '{"type":"thinking_delta","text":"Since their book is empty, I should mention connecting accounts first."}',
        '{"type":"text_delta","text":"A few practical layers, from easiest to most hands"}',
        '{"type":"text_delta","text":"-off: connect accounts directly."}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('super-1').messages
    expect(msgs.filter((m) => m.role === 'assistant')).toHaveLength(1)
    expect(msgs[0]?.thinking).toBe(
      'Since their book is empty, I should mention connecting accounts first.',
    )
    expect(msgs[0]?.text).toBe(
      'A few practical layers, from easiest to most hands-off: connect accounts directly.',
    )
    // One thinking block, one text block — not doubled.
    expect(msgs[0]?.blocks).toHaveLength(2)
  })

  it('tool_done reaches its block even when it ended up on an earlier message', async () => {
    // Defensive path: updateToolBlockAnywhere must not assume "the last message" —
    // simulates a block that (for whatever reason) sits on a non-last message.
    chatSessions.apply('anywhere-1', (s) => ({
      ...s,
      messages: [
        {
          ...assistantMsg('a1', false),
          blocks: [{ type: 'tool', id: 'tu-old', name: 'get_transactions', status: 'executing' }],
        },
        assistantMsg('a2', true),
      ],
    }))

    await consumeEventStream(
      'anywhere-1',
      sseResponse(['{"type":"tool_done","id":"tu-old","result":"late result"}']),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('anywhere-1').messages
    const block = msgs[0]?.blocks?.[0]
    expect(block).toMatchObject({ status: 'done', result: 'late result' })
  })
})
