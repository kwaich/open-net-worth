import { describe, expect, it } from 'vitest'
import type { VaultItem } from '../../api/vault'
import {
  fileType,
  formatDay,
  formatSize,
  hasTag,
  matchesQuery,
  tagCounts,
  removalActions,
  vaultSublabel,
} from './vaultFormat'

const base: VaultItem = {
  id: 'file-att_1',
  kind: 'file',
  source_id: 'att_1',
  title: 'W-2',
  filename: 'w2.pdf',
  media_type: 'application/pdf',
  size_bytes: 1536,
  added_at: '2026-03-02T09:00:00Z',
  year: 2025,
  private: false,
  file_url: '/api/uploads/att_1/file',
  origin: 'vault',
}

describe('vaultFormat', () => {
  it('sizes read in the unit a person would use', () => {
    expect(formatSize(null)).toBe('')
    expect(formatSize(512)).toBe('512 B')
    expect(formatSize(1536)).toBe('2 KB')
    expect(formatSize(3 * 1024 * 1024)).toBe('3.0 MB')
  })

  it('a file row says it is an upload, and the year it is for', () => {
    // Every row ends with the day it was added — the list's own order.
    const added = formatDay(base.added_at)
    expect(vaultSublabel(base)).toBe(`Upload 2025 · PDF · ${added}`)
    expect(vaultSublabel({ ...base, year: null })).toBe(`Upload · PDF · ${added}`)
  })

  it('a row names the file type — from the extension, else the media type', () => {
    expect(fileType({ ...base, filename: 'history.xlsx' })).toBe('XLSX')
    expect(fileType({ ...base, filename: 'photo.jpeg' })).toBe('JPG')
    expect(fileType({ ...base, filename: 'scan', media_type: 'image/png' })).toBe('PNG')
    expect(fileType({ ...base, filename: '', media_type: 'application/octet-stream' })).toBeNull()
    expect(fileType({ ...base, kind: 'artifact', filename: 'review.html' })).toBeNull()
    expect(vaultSublabel({ ...base, kind: 'statement', size_bytes: 175_000 })).toBe(
      `Statement · PDF · ${formatDay(base.added_at)}`,
    )
    expect(
      vaultSublabel({
        ...base,
        kind: 'receipt',
        transaction_date: '2026-01-05',
        filename: 'r.jpg',
      }),
    ).toBe(`Receipt · JPG · ${formatDay(base.added_at)}`)
  })

  it('offers the removals each kind allows', () => {
    const labels = (item: typeof base) => removalActions(item).map((a) => a.label)
    expect(labels(base)).toEqual(['Delete file'])
    expect(labels({ ...base, origin: 'chat' })).toEqual(['Remove from Documents', 'Delete file'])
    expect(removalActions({ ...base, origin: 'chat' }).map((a) => !!a.deleteFile)).toEqual([
      false,
      true,
    ])
    expect(labels({ ...base, kind: 'artifact' })).toEqual(['Remove from Documents'])
    expect(labels({ ...base, kind: 'artifact', detached: true })).toEqual(['Delete'])
    expect(labels({ ...base, kind: 'statement' })).toEqual(['Delete statement'])
    expect(labels({ ...base, kind: 'receipt' })).toEqual(['Delete receipt'])
    expect(labels({ ...base, kind: 'asset' })).toEqual(['Delete file'])
    expect(removalActions({ ...base, kind: 'asset', asset_name: 'Flat' })[0]?.body).toMatch(/Flat/)
  })

  it('search matches title, file name and account', () => {
    expect(matchesQuery(base, 'w-2')).toBe(true)
    expect(matchesQuery(base, 'W2.PDF')).toBe(true)
    expect(matchesQuery({ ...base, account_name: 'Everyday' }, 'every')).toBe(true)
    expect(matchesQuery(base, 'mortgage')).toBe(false)
    expect(matchesQuery({ ...base, note: 'Original in the SAFE' }, 'safe')).toBe(true)
    expect(matchesQuery({ ...base, tags: ['Apartment'] }, 'apart')).toBe(true)
  })
})

describe('tags', () => {
  it('counts every tag in use, most used first — case never splits one', () => {
    const items = [
      { ...base, tags: ['Apartment', 'Taxes 2025'] },
      { ...base, tags: ['apartment'] },
      { ...base, tags: ['Car'] },
      { ...base, tags: undefined },
    ]
    expect(tagCounts(items)).toEqual([
      { tag: 'Apartment', count: 2 },
      { tag: 'Car', count: 1 },
      { tag: 'Taxes 2025', count: 1 },
    ])
    expect(hasTag(items[1]!, 'APARTMENT')).toBe(true)
    expect(hasTag(items[3]!, 'Car')).toBe(false)
  })
})
