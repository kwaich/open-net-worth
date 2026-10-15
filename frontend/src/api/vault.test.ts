// @vitest-environment jsdom
//
// An asset file's name and note are its asset page's too: a rename or a note written in
// Documents announces `assets`, so that page refreshes. Synthetic data only.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renameVaultItem, setVaultNote, type VaultItem } from './vault'

const { authFetch, emitDataChanged } = vi.hoisted(() => ({
  authFetch: vi.fn(),
  emitDataChanged: vi.fn(),
}))
vi.mock('./client', () => ({ authFetch }))
vi.mock('../lucius/bus', () => ({ emitDataChanged }))

function answer(kind: VaultItem['kind']): Response {
  const item = { id: `${kind}-x`, kind, title: 'Payment Schedule', note: 'Signed copy' }
  return { ok: true, status: 200, json: async () => ({ item }) } as Response
}

beforeEach(() => {
  authFetch.mockReset()
  emitDataChanged.mockReset()
})

describe('writes on an asset file announce the asset page', () => {
  it('a note or a rename on an asset file announces assets', async () => {
    authFetch.mockResolvedValueOnce(answer('asset')).mockResolvedValueOnce(answer('asset'))
    expect((await setVaultNote('asset-x', 'Signed copy')).note).toBe('Signed copy')
    await renameVaultItem('asset-x', 'Payment Schedule')
    expect(emitDataChanged.mock.calls).toEqual([[['assets']], [['assets']]])
  })

  it('any other kind announces nothing', async () => {
    authFetch.mockResolvedValueOnce(answer('statement')).mockResolvedValueOnce(answer('file'))
    await setVaultNote('statement-x', 'Paid from the joint account')
    await renameVaultItem('file-x', 'Deed')
    expect(emitDataChanged).not.toHaveBeenCalled()
  })
})
