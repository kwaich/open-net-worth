// Price-alert model shared by the Manage view, the create/edit form and the modal that hosts
// them — a faithful port of the "Price Alerts Redesign" prototype's constants and derivations.
// Pure: no React, no DOM, no network (the fetch helpers at the bottom are the one seam).
import { authFetch } from '../../../api/client'

export type StoredOp = '>=' | '<=' | '>' | '<'
export type Op = '>=' | '<='
export type AlertKind = 'price' | 'move'
// What happens after this alert fires: 'off' = turn off until re-enabled; otherwise the number
// of cooldown minutes to stay quiet before re-arming ('0' = none — may fire repeatedly).
export type AfterFire = 'off' | '0' | '15' | '30' | '60' | '360' | '720' | '1440'

export interface AlertRecord {
  id?: string
  ticker?: string
  op: StoredOp
  /** A price level for kind 'price'; a percent-of-day-move threshold for kind 'move'. */
  value: number
  name?: string
  kind?: AlertKind
  /** Where this alert is also sent (channel ids); [] = in-app only. */
  channels?: string[]
  note?: string
  label?: string
  asset_class?: string
  instrument_id?: string
  on_trigger?: 'cooldown' | 'disable'
  cooldown_minutes?: number
  enabled?: boolean
  last_fired_at?: string | null
  created_at?: string
}

/** The form's working copy of one alert (new or existing). */
export interface AlertDraft {
  id: string | null
  name: string
  mode: AlertKind
  op: Op
  /** The op as stored ('>'/'<' fold onto the two directions above) — an untouched strict-op
   * alert round-trips losslessly; a changed direction discards it. */
  origOp?: StoredOp
  /** Target price when mode is 'price'. */
  value: number | null
  /** Percent move when mode is 'move'. */
  move: number
  after: AfterFire
  /** The alert's own channels; null on a new alert = start from Settings' price-alert default. */
  channels: string[] | null
  note: string
  enabled: boolean
}

export const AFTER: [AfterFire, string][] = [
  ['off', 'Turn off'],
  ['0', 'No cooldown'],
  ['15', '15 min'],
  ['30', '30 min'],
  ['60', '1 hour'],
  ['360', '6 hours'],
  ['720', '12 hours'],
  ['1440', '24 hours'],
]
export const AFTER_SHORT: Record<AfterFire, string> = {
  '15': '15 min cooldown',
  '30': '30 min cooldown',
  '60': '1 hour cooldown',
  '360': '6 hour cooldown',
  '720': '12 hour cooldown',
  '1440': '24 hour cooldown',
  '0': 'No cooldown',
  off: 'Turns off after firing',
}
export const OPSYM: Record<Op, string> = { '>=': '≥', '<=': '≤' }
/** How often a ticker's custom feeds are fetched: the hot RSS lane's fixed tick
 * (`backend/scheduler.py::_HOT_INTERVAL_SECONDS`), which stops when RSS freshness is off. */
export const CUSTOM_FEED_POLL_SECONDS = 90
/** Within this many percent of firing, a row and its tick on the track turn amber. */
export const NEAR = 5

export const fmt = (n: number | null | undefined): string =>
  Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** Fold the stored 4-op vocabulary onto the 2-direction one the UI presents. */
export const foldOp = (op: StoredOp): Op => (op === '<=' || op === '<' ? '<=' : '>=')

/** Percent the price still has to travel to fire — negative once it is past the level. */
export const toFire = (op: Op, value: number, price: number): number =>
  ((op === '>=' ? value - price : price - value) / price) * 100

export function afterFireFromCondition(a: {
  on_trigger?: 'cooldown' | 'disable'
  cooldown_minutes?: number
}): AfterFire {
  if (a.on_trigger === 'disable') return 'off'
  const presets: AfterFire[] = ['0', '15', '30', '60', '360', '720', '1440']
  const s = String(a.cooldown_minutes ?? 1440)
  return (presets as string[]).includes(s) ? (s as AfterFire) : '1440'
}

export function afterFireToCondition(after: AfterFire): {
  on_trigger: 'cooldown' | 'disable'
  cooldown_minutes: number
} {
  return after === 'off'
    ? { on_trigger: 'disable', cooldown_minutes: 1440 }
    : { on_trigger: 'cooldown', cooldown_minutes: Number(after) }
}

/** A row's primary line is the alert's own short name. Unnamed alerts fall back to their
 * condition rather than to a clause scraped out of the instruction text. */
export function rowTitle(r: AlertRecord, ccy: string): string {
  const name = (r.name || '').trim()
  if (name) return name
  const op = foldOp(r.op)
  if (r.kind === 'move') return `${op === '>=' ? 'Rises' : 'Falls'} ${fmt(r.value)}% in a day`
  return `${op === '>=' ? 'Rises above ' : 'Falls below '}${ccy}${fmt(r.value)}`
}

/** The compact list chip: `≥ $182` for a level, `↑ 5%` for a day move (raw value, no padding). */
export function alertChipText(a: Pick<AlertRecord, 'op' | 'value' | 'kind'>, sym: string): string {
  const up = a.op === '>=' || a.op === '>'
  if (a.kind === 'move') return `${up ? '↑' : '↓'} ${a.value}%`
  return `${OPSYM[foldOp(a.op)]} ${sym}${a.value}`
}

/** The condition chip: `≥ $182.00` for a level, `↑ 5.00%` for a day move. */
export function conditionChip(r: AlertRecord): { sym: string; text: string } {
  const op = foldOp(r.op)
  if (r.kind === 'move') return { sym: op === '>=' ? '↑' : '↓', text: `${fmt(r.value)}%` }
  return { sym: OPSYM[op], text: fmt(r.value) }
}

/** Lay the tick labels out so neighbours never overprint: each label takes the first of three
 * rows where its estimated box clears the previous label on that row; when none clears (a
 * pile-up tighter than three rows can hold) it takes the row with the most room. Pure —
 * `width` is the track's pixel width (0 = unmeasured, everything stays on row 0, the
 * prototype's own layout). Returns one row index per input label, input order preserved. */
export function layoutTickRows(
  labels: { pct: number; text: string }[],
  width: number,
  charPx = 6.8,
  gapPx = 12,
): number[] {
  const rows = new Array<number>(labels.length).fill(0)
  if (width <= 0) return rows
  const order = labels.map((l, i) => ({ ...l, i })).sort((a, b) => a.pct - b.pct)
  const rowEnd = [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY]
  for (const l of order) {
    const half = (l.text.length * charPx + gapPx) / 2
    const x = (l.pct / 100) * width
    let row = rowEnd.findIndex((end) => x - half >= end)
    if (row < 0) row = rowEnd.indexOf(Math.min(...rowEnd))
    rows[l.i] = row
    rowEnd[row] = x + half
  }
  return rows
}

export function draftFromRecord(r: AlertRecord): AlertDraft {
  const mode: AlertKind = r.kind === 'move' ? 'move' : 'price'
  return {
    id: r.id ?? null,
    name: r.name || '',
    mode,
    op: foldOp(r.op),
    origOp: r.op,
    value: mode === 'price' ? r.value : null,
    move: mode === 'move' ? r.value : 5,
    after: afterFireFromCondition(r),
    channels: r.channels ?? [],
    note: r.note || '',
    enabled: r.enabled !== false,
  }
}

/** The body the API stores for a draft — the SAME shape for create (POST) and edit (PATCH). */
export function draftToBody(d: AlertDraft): Record<string, unknown> {
  return {
    name: d.name.trim(),
    kind: d.mode,
    op: d.origOp && foldOp(d.origOp) === d.op ? d.origOp : d.op,
    value: d.mode === 'move' ? d.move : (d.value ?? 0),
    note: d.note.trim(),
    ...(d.channels ? { channels: d.channels } : {}),
    enabled: d.enabled,
    ...afterFireToCondition(d.after),
  }
}

// ── API seam ─────────────────────────────────────────────────────────────────────────────

const json = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export async function listAlerts(ticker: string): Promise<AlertRecord[]> {
  const res = await authFetch(`/api/alerts?ticker=${encodeURIComponent(ticker)}`)
  if (!res.ok) throw new Error('list alerts failed')
  const data = (await res.json()) as { alerts?: AlertRecord[] }
  return data.alerts ?? []
}

export async function createAlert(body: Record<string, unknown>): Promise<void> {
  const res = await authFetch('/api/alerts', { method: 'POST', ...json(body) })
  if (!res.ok) throw new Error('create alert failed')
}

export async function patchAlert(id: string, body: Record<string, unknown>): Promise<void> {
  const res = await authFetch(`/api/alerts/${id}`, { method: 'PATCH', ...json(body) })
  if (!res.ok) throw new Error('update alert failed')
}

export async function deleteAlert(id: string): Promise<void> {
  const res = await authFetch(`/api/alerts/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('delete alert failed')
}
