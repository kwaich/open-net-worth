import type { VaultItem, VaultKind } from '../../api/vault'
import { fDate } from '../../utils/format'

export type VaultFilter = 'all' | VaultKind

const TABLE_TYPES = [
  'text/csv',
  'application/csv',
  'text/tab-separated-values',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
]

/** A CSV or Excel file — previewed as a grid (services/document_tables.py). */
export function isTable(item: VaultItem): boolean {
  const name = (item.filename || '').toLowerCase()
  return (
    TABLE_TYPES.includes((item.media_type || '').toLowerCase()) ||
    /\.(csv|tsv|xlsx|xls)$/.test(name)
  )
}

export const VAULT_FILTERS: { key: VaultFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'file', label: 'Uploads' },
  { key: 'statement', label: 'Statements' },
  { key: 'receipt', label: 'Receipts' },
  { key: 'asset', label: 'Assets' },
  { key: 'artifact', label: 'Artifacts' },
]

// What the shared upload parser (services/attachment_parse.py) can read — anything else is
// refused with a 415, so the picker doesn't offer it.
export const VAULT_ACCEPT = 'image/*,.pdf,.txt,.csv,.xlsx,.xls'

export function formatSize(bytes: number | null | undefined): string {
  if (!bytes || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** "12 Mar 2026" from a date or an ISO timestamp (read as the calendar day it names). */
export function formatDay(iso: string | null | undefined): string {
  const day = (iso || '').slice(0, 10)
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? fDate(day, 'short', 'always') : ''
}

export function kindLabel(item: VaultItem): string {
  if (item.kind === 'file') return 'Upload'
  if (item.kind === 'statement') return 'Statement'
  if (item.kind === 'receipt') return 'Receipt'
  if (item.kind === 'asset') return item.asset_name || 'Asset file'
  return 'Saved from chat'
}

/** The row's second line — what it is and the one date that matters for it. */
const TYPE_BY_MEDIA: Record<string, string> = {
  'application/pdf': 'PDF',
  'image/jpeg': 'JPG',
  'image/png': 'PNG',
  'image/heic': 'HEIC',
  'image/webp': 'WEBP',
  'text/csv': 'CSV',
  'text/plain': 'TXT',
  'application/vnd.ms-excel': 'XLS',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'XLSX',
}

/** The file's type as a short label — its extension, else its media type ("PDF", "CSV").
 *  Names drop the extension, so the row says it here. An artifact is always a page from chat. */
export function fileType(item: VaultItem): string | null {
  if (item.kind === 'artifact') return null
  const ext = /\.([a-z0-9]{1,5})$/i.exec(item.filename || '')?.[1]
  if (ext) return ext.toUpperCase() === 'JPEG' ? 'JPG' : ext.toUpperCase()
  return TYPE_BY_MEDIA[(item.media_type || '').toLowerCase()] ?? null
}

/** The row's second line — what it is, its file type, and the day it was ADDED to Documents:
 *  the one date every item has, and the list's own order (newest added first). A statement's
 *  month is in its name and a receipt's purchase date on its page; the list no longer mixes
 *  those with sizes and years (owner: "why size for some, date for others? what date is it?").
 *  An upload's year, when you set one, stays with its type: "Tax 2025". */
export function vaultSublabel(item: VaultItem): string {
  const kind =
    item.kind === 'file' && item.year ? `${kindLabel(item)} ${item.year}` : kindLabel(item)
  return [kind, fileType(item), formatDay(item.added_at)].filter(Boolean).join(' · ')
}

// Plain text, and the bank exports that are text inside (OFX/QFX/QBO/QIF, MT940/STA, camt XML).
const TEXT_EXT =
  /\.(txt|text|md|markdown|log|json|xml|ya?ml|ini|cfg|conf|ofx|qfx|qbo|qif|mt940|sta|camt)$/

/** A file to show as text — never a sheet, which has its own preview. */
export function isText(item: VaultItem): boolean {
  if (isTable(item)) return false
  const media = (item.media_type ?? '').toLowerCase()
  return TEXT_EXT.test(item.filename.toLowerCase()) || media.startsWith('text/')
}

export function isImage(item: VaultItem): boolean {
  return (item.media_type ?? '').startsWith('image/')
}

export function isPdf(item: VaultItem): boolean {
  return (
    (item.media_type ?? '').toLowerCase() === 'application/pdf' ||
    item.filename.toLowerCase().endsWith('.pdf')
  )
}

export function matchesQuery(item: VaultItem, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return [
    item.title,
    item.filename,
    item.account_name,
    item.asset_name,
    item.note,
    ...(item.tags ?? []),
  ]
    .filter(Boolean)
    .some((s) => String(s).toLowerCase().includes(q))
}

export function hasTag(item: VaultItem, tag: string): boolean {
  const want = tag.toLowerCase()
  return (item.tags ?? []).some((t) => t.toLowerCase() === want)
}

/** Every tag in use, with how many items carry it — most used first. Case never splits a
 *  tag: the first spelling met names it. */
export function tagCounts(items: VaultItem[]): { tag: string; count: number }[] {
  const byKey = new Map<string, { tag: string; count: number }>()
  for (const item of items)
    for (const tag of item.tags ?? []) {
      const key = tag.toLowerCase()
      const seen = byKey.get(key)
      if (seen) seen.count += 1
      else byKey.set(key, { tag, count: 1 })
    }
  return [...byKey.values()].sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag))
}

export interface RemovalAction {
  label: string
  body: string
  /** Delete the user's file for good rather than just take it out of Documents. */
  deleteFile?: boolean
  /** Only takes it out of Documents — the original stays where it came from (not destructive). */
  keeps?: boolean
}

/** The ways this item can be removed here. A file kept from a chat offers both: out of
 *  Documents (it stays in its chat), or deleted for good. A statement, receipt or asset file
 *  is listed on its own, so removing one IS deleting it — exactly as its own screen does. */
export function removalActions(item: VaultItem): RemovalAction[] {
  if (item.kind === 'statement') {
    return [
      {
        label: 'Delete statement',
        body: 'The statement and its file are deleted. The transactions it imported stay in your accounts.',
      },
    ]
  }
  if (item.kind === 'receipt') {
    return [
      {
        label: 'Delete receipt',
        body: 'The receipt is deleted and taken off its transaction. The transaction stays.',
      },
    ]
  }
  if (item.kind === 'asset') {
    const asset = item.asset_name || 'its asset'
    return [{ label: 'Delete file', body: `The file is deleted and taken off ${asset}.` }]
  }
  if (item.kind === 'artifact') {
    return item.detached
      ? [
          {
            label: 'Delete',
            body: 'The conversation that made it is gone, so this deletes it for good.',
          },
        ]
      : [
          {
            label: 'Remove from Documents',
            body: 'It stays in the conversation that made it.',
            keeps: true,
          },
        ]
  }
  if (item.origin === 'chat') {
    return [
      {
        label: 'Remove from Documents',
        body: item.private
          ? 'The file stays in the chat you sent it in — still Private.'
          : 'The file stays in the chat you sent it in.',
        keeps: true,
      },
      {
        label: 'Delete file',
        body: 'The file is deleted for good — the chat you sent it in can no longer open or read it.',
        deleteFile: true,
      },
    ]
  }
  return [
    {
      label: 'Delete file',
      body: 'The file is deleted for good — Documents is the only place it lives.',
      deleteFile: true,
    },
  ]
}
