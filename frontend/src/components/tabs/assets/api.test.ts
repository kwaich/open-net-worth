// The assets write helpers: request bodies (the asset and document PATCH routes take a
// `{ changes }` envelope — the first port sent the flat field set, so every "Save changes" was
// a 422), the one error parser behind every write, the inline loan's sign, and the data-changed
// announcements every mounted reader refetches on.
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { authFetch, emitDataChanged, invalidateQueryCache } = vi.hoisted(() => ({
  authFetch: vi.fn(),
  emitDataChanged: vi.fn(),
  invalidateQueryCache: vi.fn(),
}))
vi.mock('../../../api/client', () => ({ authFetch }))
vi.mock('../../../lucius/bus', () => ({ emitDataChanged }))
vi.mock('../../../state/queryCache', () => ({ invalidateQueryCache }))

import {
  AssetsApiError,
  addMark,
  correctMark,
  createAsset,
  createCategory,
  createLoanAccount,
  deleteAsset,
  disposeAsset,
  removeMark,
  updateAsset,
  updateDoc,
} from './api'

function ok(data: unknown): Response {
  return { ok: true, status: 200, json: async () => data } as Response
}

function failed(status: number, body: unknown): Response {
  return { ok: false, status, json: async () => body } as Response
}

function sentBody(call = 0): Record<string, unknown> {
  const init = authFetch.mock.calls[call]![1] as RequestInit
  return JSON.parse(String(init.body)) as Record<string, unknown>
}

async function caught(p: Promise<unknown>): Promise<AssetsApiError> {
  const err = await p.then(
    () => null,
    (e: unknown) => e,
  )
  expect(err).toBeInstanceOf(AssetsApiError)
  return err as AssetsApiError
}

beforeEach(() => {
  authFetch.mockReset()
  emitDataChanged.mockReset()
  invalidateQueryCache.mockReset()
  authFetch.mockResolvedValue(ok({}))
})

describe('assets api write bodies', () => {
  it('updateAsset wraps identity edits in a changes envelope', async () => {
    await updateAsset('oa_1', { name: 'Flat', share_pct: 50 })
    expect(authFetch.mock.calls[0]![0]).toBe('/api/assets/oa_1')
    expect((authFetch.mock.calls[0]![1] as RequestInit).method).toBe('PATCH')
    expect(sentBody()).toEqual({ changes: { name: 'Flat', share_pct: 50 } })
  })

  it('updateDoc wraps the readable flag in a changes envelope', async () => {
    await updateDoc('oa_1', 'att_1', { lucius_readable: false })
    expect(authFetch.mock.calls[0]![0]).toBe('/api/assets/oa_1/docs/att_1')
    expect(sentBody()).toEqual({ changes: { lucius_readable: false } })
  })

  it('values, disposal and categories post their bodies flat', async () => {
    await addMark('oa_1', { value: 16400, note: 'listing', date: '2026-09-01' })
    expect(sentBody()).toEqual({ value: 16400, note: 'listing', date: '2026-09-01' })
    authFetch.mockClear()
    await disposeAsset('oa_1', { proceeds: 15000, account_id: 'acc_1', date: '2026-09-20' })
    expect(sentBody()).toEqual({ proceeds: 15000, account_id: 'acc_1', date: '2026-09-20' })
    authFetch.mockClear()
    await createCategory('Boats')
    expect(sentBody()).toEqual({ label: 'Boats' })
  })

  it('correctMark PATCHes the one value with only the fields that changed', async () => {
    await correctMark('oa_1', 'mk_a1b2', { value: 16400 })
    expect(authFetch.mock.calls[0]![0]).toBe('/api/assets/oa_1/marks/mk_a1b2')
    expect((authFetch.mock.calls[0]![1] as RequestInit).method).toBe('PATCH')
    expect(sentBody()).toEqual({ value: 16400 })
  })

  it('removeMark DELETEs the one value and returns the asset', async () => {
    authFetch.mockResolvedValue(ok({ id: 'oa_1' }))
    const asset = await removeMark('oa_1', 'mk_a1b2')
    expect(authFetch.mock.calls[0]![0]).toBe('/api/assets/oa_1/marks/mk_a1b2')
    expect((authFetch.mock.calls[0]![1] as RequestInit).method).toBe('DELETE')
    expect(asset).toEqual({ id: 'oa_1' })
  })

  it('createAsset posts the wire body as given', async () => {
    const body = {
      name: '88 Marina Rise',
      category: 'property',
      value: 1150000,
      share_pct: 50,
      share_note: 'Joint with A. Lim',
      loan_account_id: 'acc_5b2e',
      cost: 900000,
      cost_date: '2019-03-15',
    }
    await createAsset(body)
    expect(authFetch.mock.calls[0]![0]).toBe('/api/assets')
    expect(sentBody()).toEqual(body)
  })
})

describe('the inline loan', () => {
  beforeEach(() => {
    authFetch.mockResolvedValue(
      ok({ id: 'acc_77c1', name: 'Car loan', balance: -38000, balance_base: -38000 }),
    )
  })

  it('posts the amount owed NEGATIVE — a positive opening would add the debt to net worth', async () => {
    await createLoanAccount({ name: '  Car loan ', owed: 38000 })
    expect(authFetch.mock.calls[0]![0]).toBe('/api/accounts')
    expect((authFetch.mock.calls[0]![1] as RequestInit).method).toBe('POST')
    expect(sentBody()).toEqual({ name: 'Car loan', type: 'loan', initial_balance: -38000 })
  })

  it('keeps the sign negative whatever the caller passes', async () => {
    await createLoanAccount({ name: 'Car loan', owed: -38000 })
    expect(sentBody().initial_balance).toBe(-38000)
  })

  it('returns a free loan the picker can select, owed as a positive balance', async () => {
    const loan = await createLoanAccount({ name: 'Car loan', owed: 38000 })
    expect(loan).toEqual({
      id: 'acc_77c1',
      name: 'Car loan',
      balance: 38000,
      rate_label: '',
      term_label: '',
      held_by: null,
    })
  })

  it('drops the cached account list and announces accounts + ledger', async () => {
    await createLoanAccount({ name: 'Car loan', owed: 38000 })
    expect(invalidateQueryCache).toHaveBeenCalledWith('/api/accounts')
    expect(emitDataChanged).toHaveBeenCalledWith(['accounts', 'ledger'])
  })
})

describe('data-changed announcements', () => {
  it('every successful asset write announces assets', async () => {
    await createAsset({ name: 'Watch', category: 'watch', value: 14800 })
    await updateAsset('oa_1', { notes: 'x' })
    await addMark('oa_1', { value: 1 })
    await correctMark('oa_1', 'mk_1', { note: 'x' })
    await removeMark('oa_1', 'mk_1')
    await deleteAsset('oa_1')
    expect(emitDataChanged.mock.calls).toEqual(Array(6).fill([['assets']]))
  })

  it('a sale also announces the ledger — it posts the proceeds', async () => {
    await disposeAsset('oa_1', { proceeds: 1, account_id: 'acc_1' })
    expect(emitDataChanged).toHaveBeenCalledWith(['assets', 'ledger'])
  })

  it('a failed write announces nothing', async () => {
    authFetch.mockResolvedValue(failed(422, { detail: 'No.' }))
    await expect(createAsset({ name: 'x', category: 'watch', value: 0 })).rejects.toThrow('No.')
    await expect(createLoanAccount({ name: 'Car loan', owed: 38000 })).rejects.toBeInstanceOf(
      AssetsApiError,
    )
    expect(emitDataChanged).not.toHaveBeenCalled()
    expect(invalidateQueryCache).not.toHaveBeenCalled()
  })
})

describe('write errors carry the server reason', () => {
  it('reads a domain error body — detail, code, reason, field and holder', async () => {
    authFetch.mockResolvedValue(
      failed(409, {
        detail:
          'Lark Mortgage already secures “88 Marina Rise”. A loan can secure one asset — unlink it there first.',
        code: 'conflict',
        reason: 'loan_in_use',
        field: 'loan_account_id',
        holder: { id: 'oa_3f9a', name: '88 Marina Rise' },
      }),
    )
    const err = await caught(createAsset({ name: 'Car', category: 'vehicle', value: 1 }))
    expect(err.message).toBe(
      'Lark Mortgage already secures “88 Marina Rise”. A loan can secure one asset — unlink it there first.',
    )
    expect(err.status).toBe(409)
    expect(err.code).toBe('conflict')
    expect(err.reason).toBe('loan_in_use')
    expect(err.field).toBe('loan_account_id')
    expect(err.holder).toEqual({ id: 'oa_3f9a', name: '88 Marina Rise' })
  })

  it('a message that belongs to the dialog has no field', async () => {
    authFetch.mockResolvedValue(
      failed(404, { detail: 'That value no longer exists.', code: 'not_found' }),
    )
    const err = await caught(removeMark('oa_1', 'mk_gone'))
    expect(err.message).toBe('That value no longer exists.')
    expect(err.field).toBeNull()
    expect(err.reason).toBeNull()
    expect(err.holder).toBeNull()
  })

  it("reads FastAPI's request-shape list: the first entry, its last request key, labelled", async () => {
    authFetch.mockResolvedValue(
      failed(422, {
        detail: [
          {
            type: 'float_parsing',
            loc: ['body', 'share_pct'],
            msg: 'Input should be a valid number',
            input: 'abc',
          },
          { type: 'missing', loc: ['body', 'name'], msg: 'Field required', input: {} },
        ],
      }),
    )
    const err = await caught(createAsset({ name: 'x', category: 'watch', value: 1 }))
    expect(err.status).toBe(422)
    expect(err.code).toBe('validation')
    expect(err.field).toBe('share_pct')
    expect(err.message).toBe('Your share: Input should be a valid number')
  })

  it('names the request key, not a list index, for a nested loc', async () => {
    authFetch.mockResolvedValue(
      failed(422, {
        detail: [{ loc: ['body', 'facts', 0], msg: 'Input should be a valid list' }],
      }),
    )
    const err = await caught(updateAsset('oa_1', { facts: [] }))
    expect(err.field).toBe('facts')
    expect(err.message).toBe('facts: Input should be a valid list')
  })

  it('a body that is not JSON still names the status', async () => {
    authFetch.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError('Unexpected token <')
      },
    } as unknown as Response)
    const err = await caught(createAsset({ name: 'x', category: 'watch', value: 1 }))
    expect(err.status).toBe(502)
    expect(err.message).toBe("That didn't save (error 502). Try again.")
    expect(err.field).toBeNull()
  })

  it('a request that never reached the server is status 0 with a connection message', async () => {
    authFetch.mockRejectedValue(new TypeError('Failed to fetch'))
    const err = await caught(addMark('oa_1', { value: 1 }))
    expect(err.status).toBe(0)
    expect(err.message).toBe("We couldn't reach the server. Check your connection and try again.")
  })

  it('deleteAsset uses the same parser', async () => {
    authFetch.mockResolvedValue(
      failed(404, { detail: "That asset ('oa_9') no longer exists.", code: 'not_found' }),
    )
    const err = await caught(deleteAsset('oa_9'))
    expect(err.status).toBe(404)
    expect(err.message).toBe("That asset ('oa_9') no longer exists.")
    expect(err.code).toBe('not_found')
  })
})
