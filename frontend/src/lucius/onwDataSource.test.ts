// @vitest-environment jsdom
/**
 * onwDataSource.test.ts — the ONW reference DataSourceAdapter.
 *
 * Regression focus: descriptors() must translate the /api/entity-types wire shape
 * (which ships the schema under `field_schema`) into the core ClientDescriptor
 * contract (`fields`).  Without this mapping every descriptor reaches the registry
 * with `fields === undefined`, and filterByDescriptor crashed every migrated tab
 * with "Cannot read properties of undefined (reading 'length')".
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

const authFetch = vi.fn()
vi.mock('../api/client', () => ({ authFetch: (...args: unknown[]) => authFetch(...args) }))

// Imported after the mock is registered.
import { onwDataSource, readEmbeddedDescriptors } from './onwDataSource'

function okJson(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response
}

describe('onwDataSource.descriptors', () => {
  beforeEach(() => authFetch.mockReset())

  it('maps the wire `field_schema` array onto the core `fields` key', async () => {
    authFetch.mockResolvedValueOnce(
      okJson([
        {
          type: 'account',
          label: 'Account',
          icon: '⬡',
          tone: 'neutral',
          field_schema: [
            { key: 'name', label: 'Name', type: 'text', sensitive: false, showInPopover: true },
            {
              key: 'balance',
              label: 'Balance',
              type: 'money',
              sensitive: false,
              showInPopover: true,
            },
          ],
        },
      ]),
    )

    const out = await onwDataSource.descriptors!()
    expect(out).toHaveLength(1)
    expect(out[0]!.type).toBe('account')
    // The critical assertion: `fields` is populated from `field_schema`, as an array.
    expect(Array.isArray(out[0]!.fields)).toBe(true)
    expect(out[0]!.fields.map((f) => f.key)).toEqual(['name', 'balance'])
  })

  it('prefers an already-canonical `fields` key when the server sends it', async () => {
    authFetch.mockResolvedValueOnce(
      okJson([
        {
          type: 'payee',
          label: 'Payee',
          icon: '◉',
          tone: 'neutral',
          fields: [
            { key: 'name', label: 'Name', type: 'text', sensitive: false, showInPopover: true },
          ],
        },
      ]),
    )
    const out = await onwDataSource.descriptors!()
    expect(out[0]!.fields.map((f) => f.key)).toEqual(['name'])
  })

  it('yields an empty fields array (never undefined) when neither key is present', async () => {
    authFetch.mockResolvedValueOnce(
      okJson([{ type: 'mystery', label: 'X', icon: '◆', tone: 'neutral' }]),
    )
    const out = await onwDataSource.descriptors!()
    expect(out[0]!.fields).toEqual([])
  })

  it('returns [] on a non-ok response', async () => {
    authFetch.mockResolvedValueOnce({ ok: false, status: 500 } as unknown as Response)
    expect(await onwDataSource.descriptors!()).toEqual([])
  })
})

describe('onwDataSource.resolve', () => {
  beforeEach(() => authFetch.mockReset())

  it('reads the BARE array the endpoint returns (regression: was reading .items → dropped all)', async () => {
    // POST /api/mentions/resolve returns [{type,ref,label,data}], not {items:[...]}.
    authFetch.mockResolvedValueOnce(
      okJson([
        { type: 'holding', ref: 'h-1', label: 'Bitcoin', data: { symbol: 'BTC', price: 59659 } },
      ]),
    )
    const out = await onwDataSource.resolve([{ type: 'holding', ref: 'h-1', label: 'Bitcoin' }])
    expect(out).toHaveLength(1)
    expect(out[0]!.data?.['symbol']).toBe('BTC')
  })

  it('also tolerates a {items:[...]} wrapper', async () => {
    authFetch.mockResolvedValueOnce(
      okJson({ items: [{ type: 'holding', ref: 'h-2', label: 'X' }] }),
    )
    expect(await onwDataSource.resolve([{ type: 'holding', ref: 'h-2', label: 'X' }])).toHaveLength(
      1,
    )
  })

  it('throws on a non-ok response (so the caller keeps its fallback)', async () => {
    authFetch.mockResolvedValueOnce({ ok: false, status: 429 } as unknown as Response)
    await expect(onwDataSource.resolve([])).rejects.toThrow()
  })
})

// ── The index.html embed — the app's only first-paint seed ──────────────────────
// backend/main.py `_shell_with_entity_types` embeds the /api/entity-types payload in the
// shell; there is no bundled copy to fall back on (ADR
// 2026-10-05-entity-types-ship-with-the-page-not-the-bundle).

describe('readEmbeddedDescriptors', () => {
  function docWith(json: string | null): Document {
    const doc = document.implementation.createHTMLDocument('shell')
    if (json !== null) {
      const el = doc.createElement('script')
      el.type = 'application/json'
      el.id = 'onw-entity-types'
      el.textContent = json
      doc.head.appendChild(el)
    }
    return doc
  }

  it('maps the embedded wire shape (field_schema) to core descriptors (fields)', () => {
    const wire = [
      {
        type: 'payee',
        label: 'Payee',
        icon: '◉',
        tone: 'invest',
        field_schema: [
          { key: 'name', label: 'Name', type: 'text', sensitive: false, showInPopover: true },
        ],
      },
    ]
    expect(readEmbeddedDescriptors(docWith(JSON.stringify(wire)))).toEqual([
      { type: 'payee', label: 'Payee', icon: '◉', tone: 'invest', fields: wire[0]!.field_schema },
    ])
  })

  it('is empty, never a throw, when the embed is absent (Vite dev server) or malformed', () => {
    expect(readEmbeddedDescriptors(docWith(null))).toEqual([])
    expect(readEmbeddedDescriptors(docWith('{not json'))).toEqual([])
    expect(readEmbeddedDescriptors(docWith('{"not":"an array"}'))).toEqual([])
  })
})
