// @vitest-environment jsdom
//
// Regression test: a new conversation resets BOTH model (already '' by construction —
// useConversations.makeConversation) and effort to the default, so the two controls in
// the model/effort pill fall back together instead of only one of them updating (owner
// report: model reset to default on a new conversation, effort silently carried over).

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { LuciusChatProvider, useLuciusChat } from './luciusChat'

const createConversation = vi.fn(() => 'new-conv-id')

vi.mock('../hooks/useConversations', () => ({
  useConversations: () => ({
    conversations: [{ id: 'c1', model: '' }],
    activeId: 'c1',
    conversationModel: '',
    updateConversationMeta: vi.fn(),
    renameConversation: vi.fn(),
    createConversation,
    ensurePersisted: vi.fn(),
    switchConversation: vi.fn(),
    deleteConversation: vi.fn(),
    setConversationModel: vi.fn(),
    confirmConversationModel: vi.fn(),
    lastViewed: {},
    touchLastViewed: vi.fn(),
    refreshConversations: vi.fn(),
  }),
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

describe('LuciusChatProvider — new conversation resets effort alongside model', () => {
  beforeEach(() => {
    localStorage.clear()
    createConversation.mockClear()
  })

  it('resets effort to the default when a new conversation is created', () => {
    localStorage.setItem('chat-effort', 'high')
    const { result } = renderHook(() => useLuciusChat(), { wrapper })
    expect(result.current.effort).toBe('high')

    act(() => {
      result.current.createConversation()
    })

    expect(createConversation).toHaveBeenCalledTimes(1)
    expect(result.current.effort).toBe('medium')
    expect(localStorage.getItem('chat-effort')).toBe('medium')
  })
})
