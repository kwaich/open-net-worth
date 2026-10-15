// @vitest-environment jsdom
//
// A follow-up set costs a model call to produce. It lived only in the in-memory slice, so a
// reload threw it away and nothing brought it back — `ChatPanel` re-fetches only for an EMPTY
// conversation, to get starter chips. Paying for the same three chips again on every reload
// is the waste; storing them is not (owner, 2026-08-23).

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { SUGGESTIONS_TIMEOUT_MS, fetchSuggestionsFor, restoreSuggestions } from './chatReducer'
import { chatSessions } from './chatSessions'
import type { Message } from '../types/chat'

const authFetch = vi.fn()
vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({
  authFetch: (...args: unknown[]) => authFetch(...args),
  setUnauthorizedHandler: vi.fn(),
}))

function seed(owner: string, lastId: string): void {
  const messages: Message[] = [
    { id: 'm-1', role: 'user', text: 'hi' },
    { id: lastId, role: 'assistant', text: 'hello' },
  ]
  chatSessions.apply(owner, (s) => ({ ...s, messages, suggestions: [], suggestionsLoading: false }))
}

function ok(suggestions: string[]) {
  authFetch.mockResolvedValue({ ok: true, json: async () => ({ suggestions }) })
}

beforeEach(() => {
  localStorage.clear()
  authFetch.mockReset()
})

describe('follow-up suggestions survive a reload', () => {
  it('restores what the conversation already paid for, with no model call', async () => {
    ok(['a', 'b'])
    seed('s-a', 'm-2')
    await fetchSuggestionsFor('s-a', true)
    expect(authFetch).toHaveBeenCalledTimes(1)

    // A reload: the slice is fresh and empty, the messages are the same.
    chatSessions.apply('s-a', (s) => ({ ...s, suggestions: [] }))
    expect(restoreSuggestions('s-a')).toBe(true)
    expect(chatSessions.getSlice('s-a').suggestions).toEqual(['a', 'b'])
    expect(authFetch).toHaveBeenCalledTimes(1) // still one — nothing was regenerated
  })

  it('ignores a set generated against a DIFFERENT exchange', async () => {
    // Another tab, another device, or a turn this browser did not see. Showing those chips
    // against an exchange they were never about is worse than showing none.
    ok(['a'])
    seed('s-b', 'm-2')
    await fetchSuggestionsFor('s-b', true)

    seed('s-b', 'm-9') // the conversation moved on
    expect(restoreSuggestions('s-b')).toBe(false)
    expect(chatSessions.getSlice('s-b').suggestions).toEqual([])
  })

  it('restores nothing when there is nothing stored', () => {
    seed('s-c', 'm-2')
    expect(restoreSuggestions('s-c')).toBe(false)
  })

  it('forgets a set once it is cleared, rather than resurrecting it', async () => {
    ok(['a'])
    seed('s-d', 'm-2')
    await fetchSuggestionsFor('s-d', true)
    // Suggestions turned off — this both clears and forgets.
    await fetchSuggestionsFor('s-d', false)
    expect(restoreSuggestions('s-d')).toBe(false)
  })

  it('survives localStorage being unavailable', async () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied')
    })
    try {
      ok(['a'])
      seed('s-e', 'm-2')
      // The chips are a convenience — never a reason for a turn to fail.
      await expect(fetchSuggestionsFor('s-e', true)).resolves.toBeUndefined()
      expect(chatSessions.getSlice('s-e').suggestions).toEqual(['a'])
      expect(restoreSuggestions('s-e')).toBe(false)
    } finally {
      getItem.mockRestore()
      setItem.mockRestore()
    }
  })
})

describe('the placeholder bars never outlive a stalled request', () => {
  it('drops the request after the timeout and clears the loading state', async () => {
    vi.useFakeTimers()
    try {
      authFetch.mockImplementation(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () => reject(new Error('aborted')))
          }),
      )
      seed('s-f', 'm-2')
      const pending = fetchSuggestionsFor('s-f', true)
      expect(chatSessions.getSlice('s-f').suggestionsLoading).toBe(true)

      await vi.advanceTimersByTimeAsync(SUGGESTIONS_TIMEOUT_MS)
      await pending
      expect(chatSessions.getSlice('s-f').suggestionsLoading).toBe(false)
      expect(chatSessions.getSlice('s-f').suggestions).toEqual([])
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('follow-up suggestions in an Automation run', () => {
  function seedRun(owner: string, replied: boolean): void {
    const messages: Message[] = [
      { id: 'r-1', role: 'user', text: 'brief', automationBrief: { name: 'Daily briefing' } },
      { id: 'r-2', role: 'assistant', text: 'Good morning.' },
    ]
    if (replied) {
      messages.push(
        { id: 'r-3', role: 'user', text: 'Could we afford a car?' },
        {
          id: 'r-4',
          role: 'assistant',
          text: 'Not yet. Pick one below, or tell me in your own words.',
        },
      )
    }
    chatSessions.apply(owner, (s) => ({
      ...s,
      messages,
      suggestions: [],
      suggestionsLoading: false,
    }))
  }

  it('makes no model call under a run nobody has replied to', async () => {
    ok(['a'])
    seedRun('run-a', false)
    await fetchSuggestionsFor('run-a', true)
    expect(authFetch).not.toHaveBeenCalled()
    expect(chatSessions.getSlice('run-a').suggestions).toEqual([])
  })

  it('offers chips once the user has replied in the run — the reply promises them', async () => {
    ok(['Trade-in is about $18k', 'What if we finance part of it?'])
    seedRun('run-b', true)
    await fetchSuggestionsFor('run-b', true)
    expect(authFetch).toHaveBeenCalledTimes(1)
    expect(chatSessions.getSlice('run-b').suggestions).toEqual([
      'Trade-in is about $18k',
      'What if we finance part of it?',
    ])
  })
})
