// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'

const clearQueryCache = vi.fn()
const clearAllMessages = vi.fn().mockResolvedValue(undefined)
vi.mock('../state/queryCache', () => ({ clearQueryCache: () => clearQueryCache() }))
vi.mock('./chatStore', () => ({ clearAllMessages: () => clearAllMessages() }))

import { isBookStorageKey, reconcileBook } from './bookIdentity'

const PREFERENCES = {
  'onw-theme': 'dark',
  'chat-open': '1',
  luciusSideCollapsed: '1',
  'onw-auth-status-v2': '{"required":true}',
  acctColW_v1: '{}',
}

function seedPreferences() {
  for (const [k, v] of Object.entries(PREFERENCES)) localStorage.setItem(k, v)
}

function seedOldBook() {
  localStorage.setItem('onw-book-id', 'old')
  localStorage.setItem('onw-query-cache:build-1', '{"/api/accounts":{}}')
  localStorage.setItem('lucius_conversation_list', '[{"id":"c1","title":"Assets page setup"}]')
  localStorage.setItem('lucius_active_conversation', 'c1')
  localStorage.setItem('onw:chat-suggestions', '{}')
  localStorage.setItem('inv-watch-list', 'w1')
  sessionStorage.setItem('documentsItem', 'doc1')
  sessionStorage.setItem('onw-chunk-reload', 'build-1')
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  clearQueryCache.mockClear()
  clearAllMessages.mockClear()
})

describe('reconcileBook', () => {
  it('drops the old book, keeps device preferences, stores the new id and reloads once', async () => {
    seedPreferences()
    seedOldBook()
    const reload = vi.fn()

    expect(await reconcileBook('new', reload)).toBe(true)

    expect(reload).toHaveBeenCalledTimes(1)
    expect(clearQueryCache).toHaveBeenCalled()
    expect(clearAllMessages).toHaveBeenCalled()
    expect(localStorage.getItem('onw-book-id')).toBe('new')
    for (const k of [
      'onw-query-cache:build-1',
      'lucius_conversation_list',
      'lucius_active_conversation',
      'onw:chat-suggestions',
      'inv-watch-list',
    ]) {
      expect(localStorage.getItem(k)).toBeNull()
    }
    for (const [k, v] of Object.entries(PREFERENCES)) expect(localStorage.getItem(k)).toBe(v)
    expect(sessionStorage.getItem('documentsItem')).toBeNull()
    expect(sessionStorage.getItem('onw-chunk-reload')).toBe('build-1')
  })

  it('does nothing when the book is unchanged, and re-arms the reload guard', async () => {
    seedOldBook()
    sessionStorage.setItem('onw-book-reload', 'old')
    const reload = vi.fn()

    expect(await reconcileBook('old', reload)).toBe(false)

    expect(reload).not.toHaveBeenCalled()
    expect(clearQueryCache).not.toHaveBeenCalled()
    expect(localStorage.getItem('lucius_active_conversation')).toBe('c1')
    expect(sessionStorage.getItem('onw-book-reload')).toBeNull()
  })

  it('a first-ever visit only stores the id', async () => {
    seedPreferences()
    localStorage.setItem('lucius_conversation_list', '[]')
    const reload = vi.fn()

    expect(await reconcileBook('first', reload)).toBe(false)

    expect(reload).not.toHaveBeenCalled()
    expect(clearQueryCache).not.toHaveBeenCalled()
    expect(localStorage.getItem('onw-book-id')).toBe('first')
  })

  it('a browser with caches but no stored id is treated as a different book', async () => {
    localStorage.setItem('lucius_conversation_list', '[{"id":"c1"}]')
    const reload = vi.fn()

    expect(await reconcileBook('new', reload)).toBe(true)

    expect(reload).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('lucius_conversation_list')).toBeNull()
  })

  it('never reloads twice for the same id', async () => {
    seedOldBook()
    sessionStorage.setItem('onw-book-reload', 'new')
    const reload = vi.fn()

    expect(await reconcileBook('new', reload)).toBe(false)

    expect(reload).not.toHaveBeenCalled()
    expect(localStorage.getItem('onw-book-id')).toBe('new')
  })

  it('a reset then a second reset each reload exactly once', async () => {
    seedOldBook()
    const reload = vi.fn()
    await reconcileBook('b2', reload)
    await reconcileBook('b2', reload)
    localStorage.setItem('lucius_conversation_list', '[{"id":"c9"}]')
    await reconcileBook('b3', reload)
    expect(reload).toHaveBeenCalledTimes(2)
  })
})

describe('isBookStorageKey', () => {
  it.each(['lucius_conversation_models', 'onw:chat-suggestions', 'onw-query-cache:x', 'wealthFX'])(
    '%s is book data',
    (key) => expect(isBookStorageKey(key)).toBe(true),
  )
  it.each(Object.keys(PREFERENCES).concat('onw-book-id', 'onw-ticker'))(
    '%s is a device preference',
    (key) => expect(isBookStorageKey(key)).toBe(false),
  )
})
