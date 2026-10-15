/**
 * objectFields.ts — Single field source for the Lucius entity detail popover.
 *
 * Architecture (mirrors prototype mentions/data.jsx):
 *
 *   parseCtxFields(jsonStr)  — parse the raw data-ctx-fields JSON attribute
 *                               into a typed Record. Shared with screenContract.ts
 *                               so the popover and the DOM snapshot use literally
 *                               the same parse — edit in one place, both update.
 *
 *   byId(type, id)           — locate the on-screen element with
 *                               data-entity-id="{type}:{id}" and parse its
 *                               data-ctx-fields. Returns null when the element
 *                               is not in the DOM (off-screen fallback).
 *
 *   objectFields(record)     — canonical [{ key, label, value, showInPopover? }]
 *                               for ANY entity type. No per-type branching in the
 *                               view layer — formatting belongs here in the data layer.
 *
 *   metaFor(record)          — popover rows: objectFields filtered to
 *                               showInPopover !== false → [[label, value]].
 *
 * Off-screen fallback:
 *   When byId() returns null (the entity row is not currently in the DOM),
 *   the caller should build a minimal record from the chip's own dataset attrs
 *   (data-chip-label, data-chip-type, chipDate, chipAmt) and pass it to
 *   objectFields/metaFor. This produces the generic fallback row set
 *   (Type + any chip-carried fields) without any network call — consistent with
 *   the handoff's "no fetch on hover" rule.
 *
 * Do NOT:
 *   - fetch on hover
 *   - hardcode row lists in the popover component
 *   - build a second field object separate from objectFields()
 *   - add type-specific display rules to MentionDetail
 */

import { fieldsFor } from './entityRegistry'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface FieldRow {
  /** Machine key from the data-ctx-fields JSON (e.g. "date", "amount", "balance"). */
  key: string
  /** Human-readable label for the popover (e.g. "Date", "Amount", "Balance"). */
  label: string
  /** Formatted value string. */
  value: string
  /**
   * When false, the row is emitted in data-ctx-fields (for the assistant snapshot)
   * but hidden from the chip-tap popover. Omit or set true to show.
   * Used for internal keys (e.g. "id", "kind") that the assistant needs but a user
   * doesn't want to see tapped out as a card row.
   */
  showInPopover?: boolean
}

export type CtxFieldRecord = Record<string, string>

// ── Helpers ────────────────────────────────────────────────────────────────────

/** Capitalize the first letter of each word in a snake_case or camelCase key. */
function toLabel(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

// ── Core: parseCtxFields ──────────────────────────────────────────────────────

/**
 * Parse the raw JSON string from a data-ctx-fields attribute into a flat
 * string record. Returns null on parse failure or when the attribute is absent.
 *
 * This is the SINGLE parse used by both the popover (objectFields) and
 * screenContract.ts (fieldStr). Exported so the two sites stay in sync.
 */
export function parseCtxFields(jsonStr: string | null | undefined): CtxFieldRecord | null {
  if (!jsonStr) return null
  try {
    const parsed = JSON.parse(jsonStr)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    // Coerce all values to strings; skip null/undefined/empty.
    const result: CtxFieldRecord = {}
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (v == null || v === '') continue
      result[k] = String(v)
    }
    return Object.keys(result).length > 0 ? result : null
  } catch {
    return null
  }
}

// ── Core: byId ────────────────────────────────────────────────────────────────

/**
 * Locate an on-screen entity element by (type, id) and parse its data-ctx-fields.
 *
 * Returns null when:
 *   - the element is not in the DOM (scrolled off, different tab, etc.)
 *   - the element has no data-ctx-fields attribute
 *   - the attribute fails to parse
 *
 * Callers must handle null gracefully via the off-screen fallback path
 * (build a minimal record from chip dataset attrs instead).
 */
export function byId(type: string, id: string): CtxFieldRecord | null {
  if (typeof document === 'undefined') return null
  const sel = `[data-entity-id="${type}:${id}"]`
  const el = document.querySelector(sel)
  if (!el) return null
  return parseCtxFields(el.getAttribute('data-ctx-fields'))
}

// ── Keys that should never surface as visible popover rows ────────────────────

// 'kind' is internal routing metadata — useful to the assistant, never user-facing.
// 'id' IS shown (users asked for it on every object) but moved to the last row.
const HIDDEN_KEYS = new Set(['kind'])

// ── Core: objectFields ────────────────────────────────────────────────────────

/**
 * Derive a canonical field list for ANY entity record.
 *
 * When the entity type has a descriptor (via entityRegistry), uses the descriptor's
 * field schema for key ordering and labels — so the popover matches the canonical
 * field set regardless of which keys the current DOM emitter provides. Falls back to
 * key-order iteration + toLabel() for unknown types or keys not in the schema.
 *
 * 'kind' is internal routing metadata — useful to the assistant, never user-facing.
 * 'id' IS shown but moved to the last row — it's a reference, not a headline field.
 */
export function objectFields(record: CtxFieldRecord, type?: string): FieldRow[] {
  const descriptorFields = type ? fieldsFor(type) : []
  const rows: FieldRow[] = []
  let idRow: FieldRow | null = null

  if (descriptorFields.length > 0) {
    // Descriptor-ordered path: emit descriptor fields in schema order, then any
    // extra keys in the record that aren't in the schema (backward compat).
    const schemaKeys = new Set(descriptorFields.map((f) => f.key))
    const emitted = new Set<string>()

    for (const f of descriptorFields) {
      const value = record[f.key]
      if (value == null) continue // skip absent keys
      emitted.add(f.key)
      if (f.key === 'id') {
        idRow = { key: f.key, label: 'ID', value, showInPopover: f.showInPopover }
        continue
      }
      rows.push({
        key: f.key,
        label: f.label || toLabel(f.key),
        value,
        showInPopover: f.showInPopover,
      })
    }

    // Extra keys in the record not covered by the descriptor schema.
    for (const [key, value] of Object.entries(record)) {
      if (schemaKeys.has(key) || emitted.has(key)) continue
      if (HIDDEN_KEYS.has(key)) continue
      if (key === 'id') {
        idRow ??= { key, label: 'ID', value, showInPopover: true }
        continue
      }
      rows.push({ key, label: toLabel(key), value, showInPopover: true })
    }
  } else {
    // Fallback path: no descriptor — iterate record keys directly.
    for (const [key, value] of Object.entries(record)) {
      const row: FieldRow = {
        key,
        label: key === 'id' ? 'ID' : toLabel(key),
        value,
        showInPopover: !HIDDEN_KEYS.has(key),
      }
      if (key === 'id') {
        idRow = row
        continue
      }
      rows.push(row)
    }
  }

  if (idRow) rows.push(idRow)
  return rows
}

// ── Core: metaFor ─────────────────────────────────────────────────────────────

/**
 * Popover rows: objectFields filtered to showInPopover !== false.
 * Returns [[label, value]] pairs ready for the detail grid render.
 * Pass `type` to use the descriptor field schema for ordering and labels.
 */
export function metaFor(record: CtxFieldRecord, type?: string): [string, string][] {
  return objectFields(record, type)
    .filter((r) => r.showInPopover !== false)
    .map((r) => [r.label, r.value])
}

// ── Off-screen fallback record builder ───────────────────────────────────────

/**
 * Build a minimal CtxFieldRecord from chip-carried data attrs when byId()
 * returns null (the entity is not currently in the DOM).
 *
 * This satisfies the "no network call" contract: the chip itself already carries
 * enough presentation data for a graceful fallback row set.
 *
 * Off-screen entities render: Type (from chip type) + any chip-carried fields
 * (Details from sublabel; Amount; Date). The assistant's data-ctx-fields snapshot
 * is unaffected — this path is purely for the popover UI.
 */
export function chipFallbackRecord(opts: {
  type: string
  label: string
  sublabel?: string
  chipDate?: string
  chipAmt?: string
}): CtxFieldRecord {
  const rec: CtxFieldRecord = {}
  rec['type'] = toLabel(opts.type)
  rec['name'] = opts.label
  if (opts.sublabel) rec['details'] = opts.sublabel
  if (opts.chipDate) rec['date'] = opts.chipDate
  if (opts.chipAmt) rec['amount'] = opts.chipAmt
  return rec
}
