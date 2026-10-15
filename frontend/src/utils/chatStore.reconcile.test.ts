import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import type { Message } from '../types/chat'

// The IndexedDB half is exercised by the app; here we only care about the
// backend-reconcile decision, so stub the transport.
const authFetch = vi.fn()
vi.mock('../api/client', () => ({ authFetch: (...args: unknown[]) => authFetch(...args) }))

const { applyRestoreResult, reconcileWithRemote, withRestoreMarks } = await import('./chatStore')

function remote(messages: Message[]) {
  authFetch.mockResolvedValue({ ok: true, json: async () => ({ messages }) })
}

const msg = (id: string): Message => ({ id, role: 'assistant', text: id }) as Message

describe('reconcileWithRemote', () => {
  beforeEach(() => authFetch.mockReset())
  afterEach(() => vi.restoreAllMocks())

  it('returns the server transcript when it is longer than what is cached', async () => {
    // The Automation-run case: the cache froze on the opening instructions while the
    // run kept working server-side.
    remote([msg('a'), msg('b'), msg('c')])
    const fresher = await reconcileWithRemote('automation-run-run_1', [msg('a')])
    expect(fresher?.map((m) => m.id)).toEqual(['a', 'b', 'c'])
  })

  it('returns null when the cache is already current', async () => {
    remote([msg('a'), msg('b')])
    expect(await reconcileWithRemote('c1', [msg('a'), msg('b')])).toBeNull()
  })

  it('never truncates: a shorter server transcript is ignored', async () => {
    // The local cache holds the richer rendered version (cards, chips) and can legitimately
    // be ahead mid-turn. Replacing it with a shorter transcript would erase live output.
    remote([msg('a')])
    expect(await reconcileWithRemote('c1', [msg('a'), msg('b')])).toBeNull()
  })

  it('hydrates a conversation with nothing cached at all', async () => {
    remote([msg('a')])
    expect((await reconcileWithRemote('c1', null))?.length).toBe(1)
  })

  it('is a no-op on a failed read rather than clearing the conversation', async () => {
    authFetch.mockResolvedValue({ ok: false })
    expect(await reconcileWithRemote('c1', [msg('a')])).toBeNull()
  })

  it('is a no-op on a network error', async () => {
    authFetch.mockRejectedValue(new Error('offline'))
    expect(await reconcileWithRemote('c1', [msg('a')])).toBeNull()
  })

  it('ignores the empty conversation id without calling the backend', async () => {
    expect(await reconcileWithRemote('', null)).toBeNull()
    expect(authFetch).not.toHaveBeenCalled()
  })
})

describe('reconcileWithRemote — an unanswered approval card', () => {
  beforeEach(() => authFetch.mockReset())

  const withCard = (id: string): Message =>
    ({ id, role: 'assistant', text: id, awaitingApproval: true }) as Message

  it('adopts a same-length transcript that carries a card the cache lacks', () => {
    // The waiting-run case: the card attaches to the LAST message rather than adding one,
    // so a length comparison alone keeps serving a cache with no way to answer it.
    remote([msg('a'), withCard('b')])
    return expect(
      reconcileWithRemote('automation-run-run_1', [msg('a'), msg('b')]).then((r) =>
        Boolean(r?.[1]?.awaitingApproval),
      ),
    ).resolves.toBe(true)
  })

  it('does not churn when the cache already shows the card', async () => {
    remote([msg('a'), withCard('b')])
    expect(await reconcileWithRemote('c1', [msg('a'), withCard('b')])).toBeNull()
  })

  it('leaves an ordinary finished conversation alone', async () => {
    remote([msg('a'), msg('b')])
    expect(await reconcileWithRemote('c1', [msg('a'), msg('b')])).toBeNull()
  })
})

describe('reconcileWithRemote — the restored state is the server’s', () => {
  beforeEach(() => authFetch.mockReset())

  const user = (id: string, extra: Partial<Message> = {}): Message =>
    ({ id, role: 'user', text: id, checkpointId: 'cp_1', ...extra }) as Message

  it('a reload over a stale cache shows Restored with its Undo', async () => {
    // Restored on another device: this browser's cache still holds the plain Restore.
    remote([user('s-1', { restored: true, undoCheckpointId: 'cp_pre' }), msg('s-2')])
    const fresher = await reconcileWithRemote('c1', [user('s-1'), msg('s-2')])
    expect(fresher?.[0]).toMatchObject({ restored: true, undoCheckpointId: 'cp_pre' })
  })

  it('a reload after Undo shows the plain Restore again', async () => {
    remote([user('s-1'), msg('s-2')])
    const fresher = await reconcileWithRemote('c1', [
      user('s-1', { restored: true, undoCheckpointId: 'cp_pre' }),
      msg('s-2'),
    ])
    expect(fresher?.[0]?.restored).toBe(false)
    expect(fresher?.[0]?.undoCheckpointId).toBeUndefined()
  })

  it('an evicted undo checkpoint keeps Restored but drops the Undo', async () => {
    remote([user('s-1', { restored: true }), msg('s-2')])
    const fresher = await reconcileWithRemote('c1', [
      user('s-1', { restored: true, undoCheckpointId: 'cp_pre' }),
      msg('s-2'),
    ])
    expect(fresher?.[0]).toMatchObject({ restored: true })
    expect(fresher?.[0]?.undoCheckpointId).toBeUndefined()
  })

  it('does not churn when the cache already agrees', async () => {
    remote([user('s-1', { restored: true, undoCheckpointId: 'cp_pre' }), msg('s-2')])
    expect(
      await reconcileWithRemote('c1', [
        user('s-1', { restored: true, undoCheckpointId: 'cp_pre' }),
        msg('s-2'),
      ]),
    ).toBeNull()
  })
})

describe('withRestoreMarks', () => {
  it('leaves messages the server snapshot does not cover alone', () => {
    const older = {
      id: 's-1',
      role: 'user',
      text: 'x',
      checkpointId: 'cp_old',
      restored: true,
    } as Message
    const local = [older]
    expect(withRestoreMarks(local, [])).toBe(local)
  })
})

describe('withRestoreMarks — superseded turns', () => {
  const user = (id: string, cp: string, extra: Partial<Message> = {}): Message =>
    ({ id, role: 'user', text: id, checkpointId: cp, ...extra }) as Message

  it('hides a later turn the server says a restore rewound past', () => {
    const local = [user('s-1', 'cp_1'), user('s-3', 'cp_2')]
    const out = withRestoreMarks(local, [
      user('s-1', 'cp_1', { restored: true, undoCheckpointId: 'cp_pre' }),
      user('s-3', 'cp_2', { checkpointSuperseded: true }),
    ])
    expect(out[1]?.checkpointSuperseded).toBe(true)
  })

  it('brings it back once the server says the restore was undone', () => {
    const local = [user('s-3', 'cp_2', { checkpointSuperseded: true })]
    const out = withRestoreMarks(local, [user('s-3', 'cp_2')])
    expect(out[0]?.checkpointSuperseded).toBe(false)
  })
})

describe('applyRestoreResult — the live tab matches a reload', () => {
  const user = (id: string, cp: string, extra: Partial<Message> = {}): Message =>
    ({ id, role: 'user', text: id, checkpointId: cp, ...extra }) as Message
  const chat = () => [user('m1', 'cp_1'), user('m2', 'cp_2'), user('m3', 'cp_3'), msg('a')]

  it('a restore marks its message and supersedes the turns it rewound past', () => {
    const out = applyRestoreResult(chat(), 'cp_1', {
      undo_checkpoint_id: 'cp_pre',
      superseded_checkpoint_ids: ['cp_2', 'cp_3'],
      revived_checkpoint_ids: [],
    })
    expect(out[0]).toMatchObject({ restored: true, undoCheckpointId: 'cp_pre' })
    expect(out.slice(1, 3).map((m) => m.checkpointSuperseded)).toEqual([true, true])
    expect(out).toHaveLength(4)
    expect(out[3]?.checkpointSuperseded).toBeUndefined()
  })

  it('an Undo flips the message back and revives the superseded turns', () => {
    const restored = applyRestoreResult(chat(), 'cp_1', {
      undo_checkpoint_id: 'cp_pre',
      superseded_checkpoint_ids: ['cp_2', 'cp_3'],
    })
    const out = applyRestoreResult(restored, 'cp_pre', {
      undo_checkpoint_id: 'cp_pre2',
      superseded_checkpoint_ids: [],
      revived_checkpoint_ids: ['cp_2', 'cp_3'],
    })
    expect(out[0]?.restored).toBe(false)
    expect(out[0]?.undoCheckpointId).toBeUndefined()
    expect(out.slice(1, 3).map((m) => m.checkpointSuperseded)).toEqual([false, false])
  })

  it('nested: undoing the outer restore leaves the inner one restored', () => {
    let c = applyRestoreResult(chat(), 'cp_3', { undo_checkpoint_id: 'cp_preA' })
    c = applyRestoreResult(c, 'cp_1', {
      undo_checkpoint_id: 'cp_preB',
      superseded_checkpoint_ids: ['cp_2', 'cp_3'],
    })
    c = applyRestoreResult(c, 'cp_preB', { revived_checkpoint_ids: ['cp_2', 'cp_3'] })
    expect(c[0]?.restored).toBe(false)
    expect(c[2]).toMatchObject({
      restored: true,
      undoCheckpointId: 'cp_preA',
      checkpointSuperseded: false,
    })
  })

  it('an older server without the id lists still marks the restored message', () => {
    const out = applyRestoreResult(chat(), 'cp_1', { undo_checkpoint_id: 'cp_pre' })
    expect(out[0]).toMatchObject({ restored: true, undoCheckpointId: 'cp_pre' })
    expect(out).toHaveLength(4)
    expect(out[1]?.checkpointSuperseded).toBeUndefined()
  })
})
