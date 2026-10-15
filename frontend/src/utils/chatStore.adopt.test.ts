import { describe, expect, it } from 'vitest'

import type { Message, MessageBlock } from '../types/chat'
import { shouldAdoptRemote } from './chatStore'

const msg = (id: string, extra: Partial<Message> = {}): Message =>
  ({ id, role: 'assistant', text: 'x', ...extra }) as Message

const blocks = (n: number): MessageBlock[] =>
  Array.from({ length: n }, () => ({ type: 'text', content: 'x' }))

describe('shouldAdoptRemote (tail-aware reconcile)', () => {
  it('never adopts an empty remote', () => {
    expect(shouldAdoptRemote([msg('s-1')], [])).toBe(false)
  })

  it('adopts when there is no local cache at all', () => {
    expect(shouldAdoptRemote(null, [msg('s-1')])).toBe(true)
  })

  it('adopts a stump repair: local frozen mid-run, remote advanced past it', () => {
    const local = [msg('s-0'), msg('s-1')]
    const remote = [msg('s-7'), msg('s-8')] // a 60-tail of the finished run
    expect(shouldAdoptRemote(local, remote)).toBe(true)
  })

  it('keeps a richer full local cache over a fresh tail ending on the same message', () => {
    const local = [msg('s-0'), msg('s-1'), msg('s-2'), msg('s-3')]
    const remote = [msg('s-2'), msg('s-3')] // tail of the same, unchanged conversation
    expect(shouldAdoptRemote(local, remote)).toBe(false)
  })

  it('adopts when the same last message gained an approval card', () => {
    const local = [msg('s-3')]
    const remote = [msg('s-3', { awaitingApproval: true } as Partial<Message>)]
    expect(shouldAdoptRemote(local, remote)).toBe(true)
  })

  // Stale-cache shape (2026-09-21): a run's first model call dies, the phone caches
  // [brief, synthetic "Connection error." bubble, live-born "Restart"], then the retry
  // succeeds server-side. The server rehydrates that whole retried turn into ONE assistant
  // message, so the tail comes back as [brief, "Restart", 42-block reply] — also length 3.
  // "Restart" is live-born (a uuid, no positional counter), so the counter branch above
  // never fires; a raw length comparison ties 3-vs-3 and the stale cache won forever.
  it('adopts over a tied length when the local side is padded by a synthetic empty bubble', () => {
    const local: Message[] = [
      msg('s-0'),
      msg('s-1', { text: '', error: 'Connection error.' }),
      msg('local-restart-uuid', { text: 'Restart' }),
    ]
    const remote: Message[] = [
      msg('s-0'),
      msg('s-1', { text: 'Restart' }),
      msg('s-2', { text: '', blocks: blocks(42) }),
    ]
    expect(shouldAdoptRemote(local, remote)).toBe(true)
  })

  // Live report 2026-10-15: a phone's POST /api/chat threw "Load failed" AFTER the server had
  // accepted it. The client painted a "Sorry, something went wrong." bubble, the live attach
  // then streamed the real turn into a SECOND bubble, and the phone froze mid-thought. The
  // server's finished transcript is [ask, reply]; the cache is [ask, failure, partial reply]
  // — one bubble longer, so every catch-up kept the cache. The failure bubble is the client's
  // own painting, never server content, and must not count.
  it('adopts the finished reply over a send that failed on the network but reached the server', () => {
    const local: Message[] = [
      msg('s-0'),
      msg('local-ask', { role: 'user', text: 'I submitted the application' }),
      msg('local-failure', {
        text: 'Sorry, something went wrong.',
        error: 'TypeError: Load failed',
      }),
      msg('local-partial', { text: '', blocks: blocks(1) }),
    ]
    const remote: Message[] = [
      msg('s-0'),
      msg('s-1', { role: 'user', text: 'I submitted the application' }),
      msg('s-2', { text: 'Here is what that means.', blocks: blocks(3) }),
    ]
    expect(shouldAdoptRemote(local, remote)).toBe(true)
  })

  // Same-id growth (owner's phone, run_47f8605fdd98, 2026-09-22): an Automation run's whole
  // multi-round turn is ONE assistant message with a stable positional id. A cache taken
  // mid-run holds `-2` with 16 blocks; the finished run's `-2` has 39. Same last id on both
  // sides, so the counter branch is skipped and the open-card check alone said "keep local"
  // — on every reopen, forever, while the server had the full run the whole time.
  it('adopts when the same last message grew more blocks on the server', () => {
    const local: Message[] = [msg('r-1'), msg('r-2', { text: '', blocks: blocks(16) })]
    const remote: Message[] = [msg('r-1'), msg('r-2', { text: 'done', blocks: blocks(39) })]
    expect(shouldAdoptRemote(local, remote)).toBe(true)
  })

  it('keeps a same-last-id local whose last message is AHEAD of the server (live turn streaming)', () => {
    const local: Message[] = [msg('r-1'), msg('r-2', { text: '', blocks: blocks(20) })]
    const remote: Message[] = [msg('r-1'), msg('r-2', { text: '', blocks: blocks(12) })]
    expect(shouldAdoptRemote(local, remote)).toBe(false)
  })

  // Guard: a tied bubble count must not truncate a local cache that is genuinely ahead —
  // e.g. a turn still streaming, with several blocks already in, against a remote tail
  // that only persisted its first round so far.
  it('keeps a tied-length local that holds more blocks than the remote (never truncate a cache that ran ahead)', () => {
    const local: Message[] = [msg('s-0'), msg('local-uuid', { text: 'go', blocks: blocks(5) })]
    const remote: Message[] = [msg('s-0'), msg('s-1', { text: '', blocks: blocks(1) })]
    expect(shouldAdoptRemote(local, remote)).toBe(false)
  })
})
