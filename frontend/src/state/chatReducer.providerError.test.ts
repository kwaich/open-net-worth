// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'
import { consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

function errorStream(message: string): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(`data: ${JSON.stringify({ type: 'error', message })}\n\n`),
        )
        controller.close()
      },
    }),
  )
}

describe('provider failure before any assistant output', () => {
  it.each(['Codex CLI not found', 'Claude Code CLI not found'])('%s is visible', async (error) => {
    chatSessions.apply(error, (slice) => ({
      ...slice,
      status: 'streaming',
      messages: [{ id: 'question', role: 'user', text: 'hello' }],
    }))

    await consumeEventStream(error, errorStream(error), { suggestionsEnabled: false })

    const slice = chatSessions.getSlice(error)
    expect(slice.messages).toHaveLength(2)
    expect(slice.messages[1]).toMatchObject({ role: 'assistant', error, streaming: false })
    expect(slice.openAssistantId).toBeNull()
    expect(slice.status).toBe('error')
    expect(slice.lastResult).toBe('error')
  })

  it('keeps an earlier successful answer unchanged', async () => {
    const id = 'provider-error-after-success'
    const answer = {
      id: 'answer',
      role: 'assistant' as const,
      text: 'Earlier answer',
      usage: { cost_usd: 0 },
    }
    chatSessions.apply(id, (slice) => ({
      ...slice,
      messages: [answer, { id: 'question', role: 'user', text: 'hello again' }],
    }))

    await consumeEventStream(id, errorStream('Provider unavailable'), { suggestionsEnabled: false })

    expect(chatSessions.getSlice(id).messages[0]).toEqual(answer)
    expect(chatSessions.getSlice(id).messages[2]?.error).toBe('Provider unavailable')
  })
})
