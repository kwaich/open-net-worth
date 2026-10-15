/**
 * harvest.ts — Framework-free DOM mention harvester.
 *
 * Extracts MentionRow candidates from the live DOM via data-entity-id
 * attributes. Pure DOM logic — no React, no ONW-domain imports.
 *
 * Extraction-readiness (§14.8 R-EX1): this module has NO ONW-domain imports
 * (@/api, @/state, @/hooks). It imports only lucius/ core modules.
 *
 * R-EX3 / F9: harvestDOM honors the shared isIgnored predicate from
 * screenContract so data-ctx-secret / data-ctx-ignore subtrees never
 * leak entity labels into the picker or recents.
 */

import type { MentionRow } from './mentionRow'
import { iconFor } from './entityRegistry'
import { sanitizeUntrusted } from './security/fence'
import { CTX_IGNORED_SELECTOR } from './screenContract'

/** True when the element (or an ancestor) is opted out of context.
 *  Same selector as screenContract.ts isIgnored — shared so every reader
 *  (snapshot, harvest, form-harvest) applies the same skip logic (F9). */
function isIgnored(el: Element): boolean {
  return !!el.closest(CTX_IGNORED_SELECTOR)
}

/**
 * Collect entities currently visible on screen via data-entity-id attributes.
 * The attribute format is "type:value" (e.g. "account:abc123").
 *
 * Skips elements inside [data-ctx-ignore] / [data-ctx-secret] / tooltip /
 * menu subtrees so secret-marked regions never leak labels into the picker
 * or recents (F9 — today harvestDOM ignored them while the snapshot engine
 * filtered; this closes that gap by applying the shared isIgnored predicate).
 */
export function harvestDOM(): MentionRow[] {
  if (typeof document === 'undefined') return []
  const rows: MentionRow[] = []
  const seen = new Set<string>()
  document.querySelectorAll<HTMLElement>('[data-entity-id]').forEach((el) => {
    // Skip opted-out subtrees (secret/ignore regions) — F9.
    if (isIgnored(el)) return
    const raw = el.dataset.entityId ?? ''
    const colon = raw.indexOf(':')
    if (colon < 1) return
    const type = raw.slice(0, colon)
    const value = raw.slice(colon + 1)
    const key = `${type}:${value}`
    if (seen.has(key)) return
    seen.add(key)
    // Sanitize all DOM-supplied strings before they enter any in-memory structure
    // (picker, recents, resolve cache).  This is the ingress fence required by
    // RFC §14.7 R-EX3 / P0-A′ — a hostile DOM cannot forge fence boundaries or
    // inject control characters via data-entity-* attributes.
    const label = sanitizeUntrusted(el.dataset.entityLabel ?? value, 200)
    const sublabel = sanitizeUntrusted(el.dataset.entitySublabel ?? '', 200)
    const rawSearch = el.dataset.entitySearch
    const search = rawSearch != null ? sanitizeUntrusted(rawSearch, 400) : undefined
    const color = el.dataset.entityColor
    rows.push({
      type,
      id: value,
      label,
      sublabel,
      value,
      icon: iconFor(type),
      onScreen: true,
      ...(search != null ? { search } : {}),
      ...(color ? { color } : {}),
    })
  })
  return rows
}

// ── Recents (REMOVED — owner decision 2026-08-11) ─────────────────────────────
//
// This module used to maintain a "recently used mentions" cache under the
// localStorage key below. It's gone: a picked mention's cached entry had no
// connection to server-side deletion, so once the entity it pointed to was
// deleted/merged away server-side, the STALE entry kept resurfacing forever —
// still selectable, in every search matching its cached label, producing a
// mention chip whose id nothing could actually resolve. There is no cache to
// go stale now; harvestDOM() only ever reads what's actually rendered right
// now, so it can't outlive what it points to.
//
// One-time cleanup: purge any leftover entry a browser accumulated before this
// removal, so it stops resurfacing without the user needing to know to clear
// localStorage themselves.
const _RETIRED_RECENTS_KEY = 'mention-recents'
if (typeof localStorage !== 'undefined') {
  try {
    localStorage.removeItem(_RETIRED_RECENTS_KEY)
  } catch {
    // Storage unavailable — nothing to clean up.
  }
}
