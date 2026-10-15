// @vitest-environment jsdom
//
// Live report 2026-08-10: asked a follow-up question while the PREVIOUS turn's answer
// was already on screen and finished. The new turn's web_search chip rendered UNDER the
// PREVIOUS answer, one turn early, before snapping to the right place once the turn
// settled.
//
// Root cause: web_search/fetch_page skip the generic tool_use_start → appendBlock path
// (their own search_start/fetch_start events instead), and those handlers wrote via
// updateLastAssistant without ensureAssistant first. updateLastAssistant finds "the last
// assistant message" by reverse scan — which skips right past the trailing NON-assistant
// message (the user question that just opened this turn) and lands on the PREVIOUS
// turn's already-finished bubble. The generic tool_use_start branch had the same defect
// in a subtler form: it wrote `toolCalls` via updateLastAssistant BEFORE appendBlock's
// own ensureAssistant call ever ran.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'
import type { Message } from '../types/chat'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

function finishedAssistant(id: string, text: string): Message {
  return {
    id,
    role: 'assistant',
    text,
    streaming: false,
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

describe("a new turn's first event opens its OWN bubble, never the previous turn's", () => {
  it('search_start (web_search) does not attach to the previous finished answer', async () => {
    chatSessions.apply('conv-search-first', (s) => ({
      ...s,
      status: 'streaming',
      messages: [
        finishedAssistant('a1', 'ONWAI x Uber answer.'),
        { id: 'u2', role: 'user', text: 'and grab x kestreleats?' },
      ],
    }))

    await consumeEventStream(
      'conv-search-first',
      sseResponse([
        '{"type":"search_start","query":"grab kestreleats taiwan"}',
        '{"type":"search_results","query":"grab kestreleats taiwan","results":[]}',
        '{"type":"text_delta","text":"Grab bought kestreleats Taiwan."}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('conv-search-first').messages
    const prior = msgs.find((m) => m.id === 'a1')!
    expect(prior.blocks ?? []).toHaveLength(0)
    expect(prior.searches ?? []).toHaveLength(0)

    const assistants = msgs.filter((m) => m.role === 'assistant')
    expect(assistants).toHaveLength(2)
    const fresh = assistants[1]!
    expect(fresh.blocks?.some((b) => b.type === 'search')).toBe(true)
    expect(fresh.text).toBe('Grab bought kestreleats Taiwan.')
  })

  it('a generic tool_use_start (get_portfolio) does not tag the previous finished answer', async () => {
    chatSessions.apply('conv-tool-first', (s) => ({
      ...s,
      status: 'streaming',
      messages: [
        finishedAssistant('a1', 'first answer'),
        { id: 'u2', role: 'user', text: "what's my portfolio now?" },
      ],
    }))

    await consumeEventStream(
      'conv-tool-first',
      sseResponse([
        '{"type":"tool_use_start","id":"tu-new-1","name":"get_portfolio"}',
        '{"type":"tool_done","id":"tu-new-1","result":"r1"}',
        '{"type":"text_delta","text":"here it is"}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('conv-tool-first').messages
    const prior = msgs.find((m) => m.id === 'a1')!
    expect(prior.toolCalls ?? []).toHaveLength(0)
    expect(prior.blocks ?? []).toHaveLength(0)

    const assistants = msgs.filter((m) => m.role === 'assistant')
    expect(assistants).toHaveLength(2)
    expect(assistants[1]?.toolCalls).toEqual(['get_portfolio'])
    expect(assistants[1]?.blocks?.some((b) => b.type === 'tool')).toBe(true)
  })

  it('thinking_delta as the first event does not tag the previous finished answer', async () => {
    chatSessions.apply('conv-think-first', (s) => ({
      ...s,
      status: 'streaming',
      messages: [
        finishedAssistant('a1', 'first answer'),
        { id: 'u2', role: 'user', text: 'a harder question' },
      ],
    }))

    await consumeEventStream(
      'conv-think-first',
      sseResponse([
        '{"type":"thinking_delta","text":"let me think..."}',
        '{"type":"text_delta","text":"the answer"}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('conv-think-first').messages
    const prior = msgs.find((m) => m.id === 'a1')!
    expect(prior.thinking ?? '').toBe('')

    const assistants = msgs.filter((m) => m.role === 'assistant')
    expect(assistants).toHaveLength(2)
    expect(assistants[1]?.thinking).toBe('let me think...')
  })

  // Live report 2026-08-25: "I'm salaried" → Lucius called update_profile with NO
  // preceding text or reasoning — the confirm card never appeared at all (not "attached
  // to the wrong bubble", genuinely dropped). update_profile was excluded from the
  // generic tool_use_start → appendBlock (→ ensureAssistant) path since this reducer's
  // very first version, with no dedicated event of its own to open a bubble instead —
  // unlike web_search/fetch_page (their own search_start/fetch_start) and activate_skill
  // (skill_loading → appendBlock). Its only downstream event, proposed_change, writes via
  // updateOpenAssistant alone, which no-ops when nothing is open. A reload always "fixed"
  // it because GET /messages returns the fully-formed persisted shape directly — this
  // test pins the LIVE, incremental path instead.
  it('update_profile as the very first event still renders its confirm card', async () => {
    chatSessions.apply('conv-update-profile-first', (s) => ({
      ...s,
      status: 'streaming',
      messages: [
        finishedAssistant('a1', 'And are you salaried, self-employed, or something else?'),
        { id: 'u2', role: 'user', text: "I'm salaried" },
      ],
    }))

    await consumeEventStream(
      'conv-update-profile-first',
      sseResponse([
        '{"type":"tool_use_start","id":"tu-up-1","name":"update_profile"}',
        '{"type":"proposed_change","tool_use_id":"tu-up-1","change":{"type":"tool_permission","grant_tool":"update_profile"}}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('conv-update-profile-first').messages
    const prior = msgs.find((m) => m.id === 'a1')!
    expect(prior.blocks ?? []).toHaveLength(0)
    expect(prior.proposedChanges ?? []).toHaveLength(0)

    const assistants = msgs.filter((m) => m.role === 'assistant')
    expect(assistants).toHaveLength(2)
    const turn = assistants[1]!
    expect(turn.blocks?.some((b) => b.type === 'tool' && b.name === 'update_profile')).toBe(true)
    expect(turn.blocks?.some((b) => b.type === 'change')).toBe(true)
    expect(turn.proposedChanges).toHaveLength(1)
  })
})
