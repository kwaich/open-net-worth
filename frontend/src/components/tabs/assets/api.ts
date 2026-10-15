// Assets tab write helpers — one function per POST/PATCH/DELETE choke on the owned-assets
// domain (services/owned_assets.py's apply_owned_asset_change, fronted by api/assets_api.py
// under /api/assets), plus the inline loan's POST /api/accounts. Every write THROWS an
// AssetsApiError on failure, carrying the server's own sentence and the request field it
// belongs beside (RFC assets-create-edit-redesign §3.1) — a dialog keeps its input and shows
// the reason next to that field; a silently-ignored 404/422 reads as a save that never
// happened. Every success announces its data domains so each mounted reader refetches (the
// accounts/transactionApi.ts convention; useNetWorth already watches 'assets').
import type { AssetHolder, AssetLoan, OwnedAsset } from '../../../types/assets'
import { authFetch } from '../../../api/client'
import { emitDataChanged } from '../../../lucius/bus'
import { invalidateQueryCache } from '../../../state/queryCache'

/** A failed write. `status` 0 means the request never reached the server. `field` is the
 *  request key the message belongs beside — absent, the message belongs to the dialog.
 *  `reason` tells 409s apart (loan_in_use, after_first_value, before_purchase,
 *  purchase_point, last_value); `holder` names the asset a loan_in_use loan secures. */
export class AssetsApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly reason: string | null
  readonly field: string | null
  readonly holder: AssetHolder | null

  constructor(
    message: string,
    status: number,
    code: string | null,
    reason: string | null,
    field: string | null,
    holder: AssetHolder | null,
  ) {
    super(message)
    this.name = 'AssetsApiError'
    this.status = status
    this.code = code
    this.reason = reason
    this.field = field
    this.holder = holder
  }
}

/** The server's caps on an asset's text, in characters (services/owned_assets_rules.py) — each
 *  field stops there rather than be refused on save. */
export const TEXT_CAPS = {
  name: 120,
  description: 300,
  fact: 120,
  valueNote: 300,
  shareNote: 200,
  notes: 4000,
  categoryLabel: 60,
  docName: 200,
  docNote: 1000,
} as const

const OFFLINE = "We couldn't reach the server. Check your connection and try again."

// Prefixes a request-shape (pydantic) error. The client validates first, so these only
// surface on a programmer error — the label still says which value was refused.
const FIELD_LABELS: Record<string, string> = {
  name: 'Name',
  value: 'Value',
  share_pct: 'Your share',
  share_note: 'Shared with',
  cost: 'Price',
  cost_date: 'Purchase date',
  date: 'Date',
  proceeds: 'Proceeds',
  initial_balance: 'Amount owed',
}

function text(v: unknown): string | null {
  return typeof v === 'string' && v ? v : null
}

function holderOf(v: unknown): AssetHolder | null {
  if (!v || typeof v !== 'object') return null
  const { id, name } = v as Record<string, unknown>
  return typeof id === 'string' && typeof name === 'string' ? { id, name } : null
}

function genericMessage(status: number): string {
  return `That didn't save (error ${status}). Try again.`
}

/** FastAPI's request-validation body: the first entry wins, and its field is the last `loc`
 *  item that names a request key (not "body", not a list index). */
function validationError(list: unknown[], status: number): AssetsApiError {
  const first = (list[0] ?? {}) as { loc?: unknown; msg?: unknown }
  const loc = Array.isArray(first.loc) ? [...first.loc].reverse() : []
  const field = loc.find((k): k is string => typeof k === 'string' && k !== 'body') ?? null
  const msg = text(first.msg) ?? genericMessage(status)
  const label = field ? (FIELD_LABELS[field] ?? field.replace(/_/g, ' ')) : null
  return new AssetsApiError(
    label ? `${label}: ${msg}` : msg,
    status,
    'validation',
    null,
    field,
    null,
  )
}

async function errorFrom(res: Response): Promise<AssetsApiError> {
  let body: unknown
  try {
    body = await res.json()
  } catch {
    return new AssetsApiError(genericMessage(res.status), res.status, null, null, null, null)
  }
  const b = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>
  if (Array.isArray(b.detail)) return validationError(b.detail, res.status)
  return new AssetsApiError(
    text(b.detail) ?? genericMessage(res.status),
    res.status,
    text(b.code),
    text(b.reason),
    text(b.field),
    holderOf(b.holder),
  )
}

async function send(path: string, init: RequestInit): Promise<Response> {
  let res: Response
  try {
    res = await authFetch(path, init)
  } catch {
    throw new AssetsApiError(OFFLINE, 0, null, null, null, null)
  }
  if (!res.ok) throw await errorFrom(res)
  return res
}

async function write<T>(path: string, init: RequestInit, domains = ['assets']): Promise<T> {
  const res = await send(path, init)
  emitDataChanged(domains)
  return (await res.json()) as T
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}

function assetPath(id: string, ...rest: string[]): string {
  return ['/api/assets', id, ...rest].map((p, i) => (i ? encodeURIComponent(p) : p)).join('/')
}

export interface CreateAssetBody {
  name: string
  category: string
  description?: string
  /** The first valuation, dated today. */
  value: number
  /** For a policy: premiums paid so far. */
  cost?: number | null
  /** For a policy: the date it started. */
  cost_date?: string | null
  share_pct?: number
  share_note?: string
  loan_account_id?: string | null
  insured?: boolean
  maturity_date?: string | null
  notes?: string
  /** The first valuation's note. */
  note?: string
  facts?: [string, string][]
}

export function createAsset(body: CreateAssetBody): Promise<OwnedAsset> {
  return write('/api/assets', json('POST', body))
}

/** The keys a section editor may change (RFC §3.4) — anything else is refused. `null` clears
 *  cost, cost_date, loan_account_id and maturity_date. The value is never one of them: record
 *  a new value (addMark) or correct one (correctMark) instead. */
export interface AssetChanges {
  name?: string
  category?: string
  description?: string
  notes?: string
  cost?: number | null
  cost_date?: string | null
  share_pct?: number
  share_note?: string
  loan_account_id?: string | null
  insured?: boolean
  maturity_date?: string | null
  facts?: [string, string][]
}

export function updateAsset(id: string, changes: AssetChanges): Promise<OwnedAsset> {
  // The router wraps identity edits as `{ changes }` (the commitments PATCH convention).
  return write(assetPath(id), json('PATCH', { changes }))
}

export async function deleteAsset(id: string): Promise<void> {
  await send(assetPath(id), { method: 'DELETE' })
  emitDataChanged(['assets'])
}

export interface AddMarkBody {
  value: number
  note?: string
  /** "As of", ISO — defaults to today on the server. */
  date?: string
}

export function addMark(id: string, body: AddMarkBody): Promise<OwnedAsset> {
  return write(assetPath(id, 'marks'), json('POST', body))
}

/** At least one field. The value keeps its id and gets `edited_at`. */
export interface CorrectMarkBody {
  value?: number
  date?: string
  note?: string
}

export function correctMark(
  id: string,
  markId: string,
  body: CorrectMarkBody,
): Promise<OwnedAsset> {
  return write(assetPath(id, 'marks', markId), json('PATCH', body))
}

export function removeMark(id: string, markId: string): Promise<OwnedAsset> {
  return write(assetPath(id, 'marks', markId), { method: 'DELETE' })
}

export interface DisposeBody {
  proceeds: number
  account_id: string
  /** "Sold on", ISO — defaults to today on the server. */
  date?: string
}

export function disposeAsset(id: string, body: DisposeBody): Promise<OwnedAsset> {
  // A sale posts a real proceeds transaction, so the ledger moves too.
  return write(assetPath(id, 'dispose'), json('POST', body), ['assets', 'ledger'])
}

function uploadFile(path: string, file: File): Promise<OwnedAsset> {
  const form = new FormData()
  form.append('file', file)
  // No Content-Type header — the browser sets multipart/form-data with the right boundary.
  return write(path, { method: 'POST', body: form })
}

export function uploadPhoto(id: string, file: File): Promise<OwnedAsset> {
  return uploadFile(assetPath(id, 'photo'), file)
}

/** No photo — its file is deleted too. */
export function removePhoto(id: string): Promise<OwnedAsset> {
  return write(assetPath(id, 'photo'), { method: 'DELETE' })
}

export function uploadDoc(id: string, file: File): Promise<OwnedAsset> {
  return uploadFile(assetPath(id, 'docs'), file)
}

export function updateDoc(
  id: string,
  ref: string,
  body: { lucius_readable?: boolean; name?: string; note?: string | null },
): Promise<OwnedAsset> {
  return write(assetPath(id, 'docs', ref), json('PATCH', { changes: body }))
}

export function removeDoc(id: string, ref: string): Promise<OwnedAsset> {
  return write(assetPath(id, 'docs', ref), { method: 'DELETE' })
}

export interface CreateCategoryResult {
  key: string
  label: string
  plural: string
  color: string
  custom: boolean
}

export function createCategory(label: string): Promise<CreateCategoryResult> {
  return write('/api/assets/categories', json('POST', { label }))
}

/** A loan the user typed into the inline "Add a loan" form. */
export interface NewLoan {
  name: string
  owed: number
}

/** The slice of POST /api/accounts's hydrated account the loan picker needs. */
interface CreatedAccount {
  id: string
  name: string
  balance: number
  balance_base?: number
}

/** Creates the inline loan through the existing accounts write choke (account, Starting
 *  Balance and pointer in one unit of work). Liabilities are stored raw-negative and the
 *  server does not guard the sign — a positive opening would ADD the debt to net worth — so
 *  the amount owed is always sent as `-Math.abs(owed)`, whatever the caller passes. */
export async function createLoanAccount({ name, owed }: NewLoan): Promise<AssetLoan> {
  const res = await send(
    '/api/accounts',
    json('POST', { name: name.trim(), type: 'loan', initial_balance: -Math.abs(owed) }),
  )
  invalidateQueryCache('/api/accounts')
  emitDataChanged(['accounts', 'ledger'])
  const account = (await res.json()) as CreatedAccount
  return {
    id: account.id,
    name: account.name,
    balance: Math.abs(account.balance_base ?? account.balance),
    rate_label: '',
    term_label: '',
    held_by: null,
  }
}

/** Cookie-authed static URL — a plain `<img src>` works, exactly like the chat's own
 *  attachment thumbnails (MessageBubble.tsx). */
export function photoUrl(ref: string): string {
  return `/api/uploads/${encodeURIComponent(ref)}/image`
}
