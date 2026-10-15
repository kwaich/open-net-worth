import { describe, it, expect } from 'vitest'
import { isChunkLoadFailure } from './lazyChunk'

describe('isChunkLoadFailure', () => {
  it("matches Chrome's dynamic-import-fetch-failed wording", () => {
    expect(
      isChunkLoadFailure(
        new Error('Failed to fetch dynamically imported module: /assets/Intel-abc123.js'),
      ),
    ).toBe(true)
  })

  it("matches Firefox's wording", () => {
    expect(isChunkLoadFailure(new Error('error loading dynamically imported module'))).toBe(true)
  })

  it("matches Safari's wording", () => {
    expect(isChunkLoadFailure(new Error('Importing a module script failed'))).toBe(true)
  })

  it('matches a webpack-style ChunkLoadError', () => {
    const err = new Error('Loading chunk 4 failed')
    err.name = 'ChunkLoadError'
    expect(isChunkLoadFailure(err)).toBe(true)
  })

  // The stale-chunk-request-served-as-HTML case (backend/main.py's _SPAStaticFiles) surfaces
  // as this MIME-mismatch wording on some engines — the actual bug report this test guards.
  it('matches the "is not a valid JavaScript MIME type" wording', () => {
    expect(isChunkLoadFailure(new Error("'text/html' is not a valid JavaScript MIME type."))).toBe(
      true,
    )
  })

  it('does not match an unrelated error', () => {
    expect(isChunkLoadFailure(new Error('Network request failed'))).toBe(false)
  })
})
