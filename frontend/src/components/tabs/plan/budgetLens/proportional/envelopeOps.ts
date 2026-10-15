// budgetLens/proportional/envelopeOps.ts — the envelope STRUCTURE edits the 50/30/20 screen offers
// (Edit envelope: name, colour, group, categories, merge, delete; Add envelope). Envelopes are
// shared with Zero-Based — "structure only, money stays with each method" (the Budget Onboarding
// (cards) design) — so these go through the SAME planning routes its budget table uses, and every
// write re-reads both methods' months.
import { authFetch } from '../../../../../api/client'
import { fetchJson } from '../../../../../hooks/serverState'
import { invalidateQueryCache } from '../../../../../state/queryCache'
import { invalidatePropMonths } from './propApi'

export interface BudgetGroupRef {
  id: string
  name: string
  /** Kept by the app (Commitments, Credit Card Payments) — nothing can be moved in. */
  synced?: boolean
}

async function send(url: string, method: string, body?: unknown): Promise<Response> {
  const r = await authFetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (r.ok) return r
  let detail = `Request failed (${r.status})`
  try {
    const b = await r.json()
    if (b?.detail) detail = String(b.detail)
  } catch {
    /* non-JSON error body */
  }
  throw new Error(detail)
}

function refreshBoth() {
  invalidatePropMonths()
  invalidateQueryCache((k) => k.startsWith('/api/planning/month'))
  invalidateQueryCache('/api/planning/groups')
  invalidateQueryCache('/api/categories')
}

const env = (id: string) => `/api/planning/envelopes/${encodeURIComponent(id)}`

export const GROUPS_KEY = '/api/planning/groups'
export const fetchGroups = () => fetchJson<BudgetGroupRef[]>(GROUPS_KEY)

export async function createEnvelope(
  name: string,
  color?: string,
  groupId?: string,
): Promise<string> {
  const r = await send('/api/planning/envelopes', 'POST', {
    name,
    ...(color ? { color } : {}),
    ...(groupId ? { group_id: groupId } : {}),
  })
  const out = (await r.json()) as { id?: string; envelope_id?: string }
  refreshBoth()
  return out.id ?? out.envelope_id ?? ''
}

export async function updateEnvelope(id: string, patch: { name?: string; color?: string }) {
  await send(env(id), 'PATCH', patch)
  refreshBoth()
}

/** Deletes the envelope FOR REAL (ADR 2026-09-26-deleting-an-envelope-deletes-it), for `month`.
 *  Returns the `undo_token` naming a server-held before-image (pass it to `undoDeleteEnvelope` to
 *  put the row, its categories, its cap and every zeroed month back in one call) alongside the
 *  REALIZED `available`/`delta_rta` — the same figure `GET .../delete-preview` showed the confirm
 *  dialog, read an instant before the write (`services.planning.envelope_delete_preview`). */
export async function deleteEnvelope(
  id: string,
  month: string,
): Promise<{ undoToken: string; available: number; deltaRta: number }> {
  const r = await send(`${env(id)}?ym=${encodeURIComponent(month)}`, 'DELETE')
  const out = (await r.json()) as { undo_token?: string; available?: number; delta_rta?: number }
  refreshBoth()
  return {
    undoToken: out.undo_token ?? '',
    available: out.available ?? 0,
    deltaRta: out.delta_rta ?? 0,
  }
}

/** Undo a delete — replays the SERVER-HELD before-image named by `token` exactly: the row
 *  (same id), its categories re-link, its target/note and 50/30/20 cap/share (keyed by the
 *  same id) come back, and every zeroed month's assigned amount is restored. ONE call — the
 *  client no longer replays the amounts itself. */
export async function undoDeleteEnvelope(token: string) {
  await send('/api/planning/envelopes/undo-delete', 'POST', { token })
  refreshBoth()
}

export async function mergeEnvelope(sourceId: string, targetId: string) {
  await send('/api/planning/envelopes/merge', 'POST', {
    source_envelope_id: sourceId,
    target_envelope_id: targetId,
  })
  refreshBoth()
}

/** Take a category out of its envelope — it becomes envelope-less (never minted a new one). */
export async function removeCategory(envelopeId: string, categoryId: string) {
  await send(`${env(envelopeId)}/split`, 'POST', { category_id: categoryId })
  refreshBoth()
}

/** Put a category in an envelope, detaching it from the one it's in first (if any). */
export async function addCategory(envelopeId: string, categoryId: string, fromEnvelopeId?: string) {
  if (fromEnvelopeId)
    await send(`${env(fromEnvelopeId)}/split`, 'POST', { category_id: categoryId })
  await send(`${env(envelopeId)}/add-category`, 'POST', { category_id: categoryId })
  refreshBoth()
}

/** A brand-new expense category; the server mints the id (and a palette colour when none). */
export async function createCategory(label: string): Promise<string> {
  const r = await send('/api/categories', 'POST', { label, kind: 'expense' })
  const out = (await r.json()) as { id?: string; key?: string }
  return out.id ?? out.key ?? ''
}

/** Move the envelope into the Zero-Based group named `name`, creating that group if needed. */
export async function moveToGroup(envelopeId: string, name: string, groups: BudgetGroupRef[]) {
  let group = groups.find((g) => g.name.trim().toLowerCase() === name.trim().toLowerCase())
  if (!group) {
    const r = await send('/api/planning/groups', 'POST', { name: name.trim() })
    group = ((await r.json()) as { group: BudgetGroupRef }).group
  }
  await send('/api/planning/groups/move-category', 'POST', {
    category: envelopeId,
    group_id: group.id,
  })
  refreshBoth()
}

/** Zero-Based's own group order — the whole of it, every group id, in order. */
export async function reorderGroups(order: string[]) {
  await send('/api/planning/groups/reorder', 'POST', { order })
  refreshBoth()
}

/** Zero-Based's own move: the envelope into `groupId`, at `index` among that group's other rows
 *  (the end when omitted) — the group is the same in both methods. */
export async function moveEnvelope(envelopeId: string, groupId: string, index?: number) {
  await send('/api/planning/groups/move-category', 'POST', {
    category: envelopeId,
    group_id: groupId,
    ...(index === undefined ? {} : { index }),
  })
  refreshBoth()
}

/** Zero-Based's own reorder: every row of `groupId`, top to bottom. */
export async function reorderInGroup(groupId: string, envelopeIds: string[]) {
  await send(`/api/planning/groups/${encodeURIComponent(groupId)}/reorder-categories`, 'POST', {
    categories: envelopeIds,
  })
  refreshBoth()
}

export async function createGroup(name: string): Promise<BudgetGroupRef> {
  const r = await send('/api/planning/groups', 'POST', { name: name.trim() })
  refreshBoth()
  return ((await r.json()) as { group: BudgetGroupRef }).group
}

export async function renameGroup(groupId: string, name: string) {
  await send(`/api/planning/groups/${encodeURIComponent(groupId)}`, 'PATCH', { name: name.trim() })
  refreshBoth()
}
