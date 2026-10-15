import { clearQueryCache } from '../state/queryCache'
import { clearAllMessages } from './chatStore'

// A browser caches things ABOUT the book: the conversation list, which conversation was open,
// the last-seen response of every screen. They are keyed by app build, not by book, so after
// the server's data is reset or a backup restored on the same address the tab kept showing the
// old book's conversations and screens. The server now names each book (`book_id` in
// /api/setup-status, new on reset and on import); the first time this tab sees a different id
// it drops what belongs to the old one and reloads onto the new book.
//
// The lists below are explicit on purpose: a device preference (theme, panel widths, column
// layout, the cached sign-in status) is not about the book and must survive.

const BOOK_ID_KEY = 'onw-book-id'
/** sessionStorage stamp: the id this tab already reloaded for, so a failed write cannot loop. */
const RELOAD_GUARD_KEY = 'onw-book-reload'

/** localStorage key families that hold book data (or ids that only mean something in it). */
const QUERY_CACHE_PREFIX = 'onw-query-cache:'
const BOOK_PREFIXES = [QUERY_CACHE_PREFIX, 'lucius_', 'onw:chat-', 'onw:plan:']
/** Individual localStorage keys that hold a book's ids or figures. */
const BOOK_KEYS = [
  'inv-watch-list',
  'intel-watchlist',
  'plan-hidden-cats',
  'onw.backgroundStatements',
  'wealthFX',
]
/** sessionStorage is per-visit navigation state (selected rows, open items) — all of it goes,
 *  except the stamps that stop a reload loop. */
const SESSION_KEEP = [
  RELOAD_GUARD_KEY,
  'onw-chunk-reload',
  'onw-gateway-reauth-at',
  'onw-reauth-drafts',
]

export function isBookStorageKey(key: string): boolean {
  return BOOK_KEYS.includes(key) || BOOK_PREFIXES.some((prefix) => key.startsWith(prefix))
}

function keysOf(storage: Storage): string[] {
  const keys: string[] = []
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i)
    if (key !== null) keys.push(key)
  }
  return keys
}

/** Does this browser hold anything a previous book left behind? A first-ever visit has at most
 *  the empty conversation list the chat writes on mount, which is not worth a reload. */
function holdsBookData(): boolean {
  if (keysOf(localStorage).some((key) => key.startsWith(QUERY_CACHE_PREFIX))) return true
  try {
    const list: unknown = JSON.parse(localStorage.getItem('lucius_conversation_list') ?? '[]')
    return !Array.isArray(list) || list.length > 0
  } catch {
    return true
  }
}

async function dropBookData(): Promise<void> {
  clearQueryCache()
  for (const key of keysOf(localStorage)) {
    if (isBookStorageKey(key)) localStorage.removeItem(key)
  }
  for (const key of keysOf(sessionStorage)) {
    if (!SESSION_KEEP.includes(key)) sessionStorage.removeItem(key)
  }
  await clearAllMessages()
}

/**
 * Compare the server's book id with the one this browser last saw. Returns true when it just
 * dropped a previous book's data and is reloading, so the caller can stop rendering.
 */
export async function reconcileBook(
  bookId: string,
  reload: () => void = () => window.location.reload(),
): Promise<boolean> {
  try {
    const stored = localStorage.getItem(BOOK_ID_KEY)
    if (stored === bookId) {
      sessionStorage.removeItem(RELOAD_GUARD_KEY)
      return false
    }
    const foreign = stored !== null || holdsBookData()
    if (foreign) await dropBookData()
    localStorage.setItem(BOOK_ID_KEY, bookId)
    if (!foreign || sessionStorage.getItem(RELOAD_GUARD_KEY) === bookId) return false
    sessionStorage.setItem(RELOAD_GUARD_KEY, bookId)
    reload()
    return true
  } catch {
    // Storage unavailable (private mode, blocked): nothing was persisted, nothing to drop.
    return false
  }
}
