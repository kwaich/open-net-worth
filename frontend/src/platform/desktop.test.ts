// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  desktopBridge,
  enqueueNativeImports,
  takeNativeImport,
  nativeImportCount,
  queueWatchedImport,
  nativeImportAttachmentId,
  markNativeImportReviewed,
  desktopArtifactUrl,
} from './desktop'
import { authFetch } from '../api/client'

vi.mock('../api/client', () => ({ authFetch: vi.fn() }))

afterEach(() => {
  delete window.onwDesktop
  vi.resetAllMocks()
  while (takeNativeImport()) {
    /* drain device choices between tests */
  }
})

describe('desktop platform', () => {
  it('keeps the browser free of desktop capabilities', () => {
    expect(desktopBridge()).toBeUndefined()
    expect(desktopArtifactUrl('art_example', 'html')).toBeUndefined()
  })

  it('queues separately chosen files without replacing the active review', () => {
    enqueueNativeImports(
      ['first.csv', 'second.csv'].map((name) => ({
        name,
        type: 'text/csv',
        bytes: new TextEncoder().encode('Date,Amount'),
      })),
    )
    expect(nativeImportCount()).toBe(2)
    expect(takeNativeImport()?.name).toBe('first.csv')
    expect(nativeImportCount()).toBe(1)
    expect(takeNativeImport()?.name).toBe('second.csv')
    expect(takeNativeImport()).toBeNull()
  })

  it('preserves watched attachment provenance and does not review it for a replacement file', async () => {
    vi.mocked(authFetch).mockResolvedValueOnce({
      ok: true,
      blob: async () => new Blob(['Date,Amount'], { type: 'text/csv' }),
    } as Response)
    await queueWatchedImport({
      id: 'job_original',
      attachment_id: 'att_private',
      filename: 'original.csv',
    })
    const original = takeNativeImport()!
    const replacement = new File(['Other data'], 'replacement.csv')
    expect(nativeImportAttachmentId(original)).toBe('att_private')
    expect(nativeImportAttachmentId(replacement)).toBeUndefined()
    expect(await markNativeImportReviewed(replacement)).toBe(true)
    expect(authFetch).toHaveBeenCalledTimes(1)
    vi.mocked(authFetch).mockResolvedValueOnce({ ok: true } as Response)
    expect(await markNativeImportReviewed(original)).toBe(true)
    expect(authFetch).toHaveBeenLastCalledWith('/api/desktop/imports/job_original', {
      method: 'PATCH',
      body: JSON.stringify({ status: 'reviewed' }),
    })
  })
})
