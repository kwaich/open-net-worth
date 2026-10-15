// @vitest-environment jsdom
//
// "New reply" must not flag an answer you watched arrive, and a read made on another device
// must reach this browser without a reload.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { LuciusChatProvider, useLuciusChat } from './luciusChat'
import { chatSessions } from './chatSessions'

let active = 'c1'
const markRead = vi.fn()
const refreshConversations = vi.fn()

vi.mock('../hooks/useConversations', () => ({
  useConversations: () => ({
    conversations: [
      { id: 'c1', model: '' },
      { id: 'c2', model: '' },
    ],
    activeId: active,
    conversationModel: '',
    updateConversationMeta: vi.fn(),
    renameConversation: vi.fn(),
    createConversation: vi.fn(),
    ensurePersisted: vi.fn(),
    switchConversation: vi.fn(),
    deleteConversation: vi.fn(),
    setConversationModel: vi.fn(),
    confirmConversationModel: vi.fn(),
    lastViewed: {},
    touchLastViewed: vi.fn(),
    markRead,
    refreshConversations,
  }),
}))

vi.mock('../api/client', () => ({
  authFetch: vi.fn(async () => new Response('{}', { status: 500 })),
  setUnauthorizedHandler: vi.fn(),
}))

vi.mock('../hooks/useModels', async (importActual) => ({
  ...(await importActual<typeof import('../hooks/useModels')>()),
  useModels: () => ({
    models: [],
    defaultModel: '',
    defaultProvider: '',
    source: 'live',
    loaded: true,
  }),
}))

vi.mock('../hooks/useChat', () => ({
  useChat: () => ({
    messages: [],
    queuedMessages: [],
    hydrated: true,
    streaming: false,
    multiTurnEnabled: false,
    sending: false,
    liveOutputTokens: 0,
    compacting: null,
    following: false,
    sendMessage: vi.fn(),
    resolvePendingToolUse: vi.fn(),
    stopStreaming: vi.fn(),
    retryLast: vi.fn(),
    canRetry: false,
    respondToChange: vi.fn(),
    previewCheckpoint: vi.fn(),
    restoreCheckpoint: vi.fn(),
    suggestions: [],
    suggestionsLoading: false,
    fetchSuggestions: vi.fn(),
  }),
}))

const wrapper = ({ children }: { children: ReactNode }) => (
  <LuciusChatProvider>{children}</LuciusChatProvider>
)

beforeEach(() => {
  localStorage.clear()
  active = 'c1'
  markRead.mockClear()
  refreshConversations.mockClear()
})

// Each mounted provider listens for focus on `window`; one left over from a previous test
// would answer this test's event too.
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('LuciusChatProvider — leaving a conversation', () => {
  it('clears the done flag of the conversation you were watching and marks it read', () => {
    const { rerender } = renderHook(() => useLuciusChat(), { wrapper })
    // The turn ended while c1 was on screen — the in-memory slice records the outcome.
    act(() => chatSessions.apply('c1', (s) => ({ ...s, lastResult: 'ok' })))
    expect(chatSessions.peekSlice('c1')?.lastResult).toBe('ok')

    active = 'c2'
    rerender()

    expect(chatSessions.peekSlice('c1')?.lastResult).toBe('none')
    expect(markRead).toHaveBeenCalledWith(['c1'])
  })

  it('does not mark anything read just for mounting', () => {
    renderHook(() => useLuciusChat(), { wrapper })
    expect(markRead).not.toHaveBeenCalled()
  })

  it('a turn that finishes AFTER you left still flags the conversation', () => {
    const { rerender } = renderHook(() => useLuciusChat(), { wrapper })
    active = 'c2'
    rerender()

    act(() => chatSessions.apply('c1', (s) => ({ ...s, lastResult: 'ok' })))

    expect(chatSessions.peekSlice('c1')?.lastResult).toBe('ok')
  })
})

describe('LuciusChatProvider — returning to the window', () => {
  it('re-reads the listing so a read made elsewhere shows up, throttled to one per burst', () => {
    renderHook(() => useLuciusChat(), { wrapper })
    const now = vi.spyOn(Date, 'now')

    now.mockReturnValue(1_000_000)
    act(() => {
      window.dispatchEvent(new Event('focus'))
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(refreshConversations).toHaveBeenCalledTimes(1)

    now.mockReturnValue(1_000_000 + 11_000)
    act(() => {
      window.dispatchEvent(new Event('focus'))
    })
    expect(refreshConversations).toHaveBeenCalledTimes(2)
  })
})
