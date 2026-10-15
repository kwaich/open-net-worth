// chipMeta.ts — shared chip decoration resolver.
//
// chipMetaFor(type, record) derives the optional date + amount fields that
// appear on a mention chip for BOTH the composer input chip (buildChipEl) and
// the sent-message output chip (MentionText → Chip). Centralising the logic
// here guarantees the two surfaces are byte-for-byte identical.
//
// Supported types:
//   transaction → chipDate (short date) + chipAmt (signed) + chipAmtNeg (direction)
//   account     → chipAmt (balance) — NEUTRAL (no pos/neg class)
//   holding     → chipAmt (price, or total value) — NEUTRAL (no pos/neg class)
//                 Reads canonical key `price` (preferred) then `value_base` (canonical
//                 backend resolve key, §6 reconciliation) then `value` (legacy DOM key).
//                 Accepts both `value_base` (post-1e emitters / backend resolve) and `value`
//                 (pre-1e DOM emitters) so the chip is correct on both paths.
//   all other   → {} (label only)
//
// chipAmtNeg is intentionally ABSENT for account and holding: undefined means
// "render without a color class" (neutral), while true/false means neg/pos.

import { byId, type CtxFieldRecord } from './objectFields'
import { fShortDate } from '../utils/format'
import { parseTransactionSublabel } from '../utils/mentionable'

export interface ChipMeta {
  chipDate?: string
  chipAmt?: string
  /** true → .chip-amt.neg (red), false → .chip-amt.pos (green), undefined → neutral */
  chipAmtNeg?: boolean
}

export function chipMetaFor(type: string, record: CtxFieldRecord): ChipMeta {
  switch (type) {
    case 'transaction': {
      const meta: ChipMeta = {}
      if (record['date']) meta.chipDate = fShortDate(record['date'])
      if (record['amount']) {
        meta.chipAmt = record['amount']
        // Accept ASCII '-' and typographic U+2212 minus.
        meta.chipAmtNeg = /^[−-]/.test(record['amount'].trim())
      }
      return meta
    }
    case 'account': {
      const bal = record['balance']
      if (bal) return { chipAmt: bal }
      return {}
    }
    case 'holding': {
      // Prefer the current per-share PRICE; fall back to total value.
      // Accept `value_base` (canonical backend resolve key, post-1e emitters) and
      // `value` (legacy DOM emitters, pre-1e migration) so the chip is correct on both.
      const val = record['price'] ?? record['value_base'] ?? record['value']
      if (val) return { chipAmt: val }
      return {}
    }
    default:
      return {}
  }
}

/**
 * The single chip-decoration resolver used by every chip surface (composer input
 * chip, drag/right-click, sent-message output chip) so they never drift.
 *
 * Transactions are special: the chip shows the NATIVE figure the user sees on screen
 * (e.g. −S$270.00), which lives in the sublabel "payee · account · date · amount".
 * The on-screen data-ctx-fields carry `amount_base` — the BASE-currency number for the
 * model, NOT the native amount — so for transactions the sublabel is authoritative and
 * chipMetaFor(record) would (a) read the wrong/absent key and (b) be base-currency.
 * Every other type's formatted figure already lives in the record (account balance,
 * holding price), so those go through chipMetaFor unchanged.
 */
export function resolveChipMeta(
  type: string,
  record: CtxFieldRecord | null,
  sublabel: string | null | undefined,
): ChipMeta {
  if (type === 'transaction') {
    if (sublabel) return parseTransactionSublabel(sublabel)
    // No sublabel (off-screen, label-only) → at least keep the date if the record has it.
    return record?.['date'] ? { chipDate: fShortDate(record['date']) } : {}
  }
  return record ? chipMetaFor(type, record) : {}
}

/**
 * Resolve chip decorations for an entity that is live in the DOM, by its id alone.
 *
 * Used by affordances that only know (type, ref) — e.g. the ✦ "Ask Lucius about this"
 * button (AskLucius.tsx), which otherwise stages a bare label-only chip. Looks the
 * element up by data-entity-id, reads its data-ctx-fields record + data-entity-sublabel,
 * and runs the unified resolveChipMeta — so the ✦ chip matches the @-picker / drag chip.
 * Returns {} when the element isn't in the DOM (off-screen → graceful label-only).
 */
export function chipMetaFromDom(type: string, ref: string): ChipMeta {
  if (typeof document === 'undefined') return {}
  const el = document.querySelector(`[data-entity-id="${type}:${ref}"]`)
  if (!el) return {}
  return resolveChipMeta(type, byId(type, ref), el.getAttribute('data-entity-sublabel'))
}
