// MentionDetail.tsx — chip detail popover.
//
// Architecture: id-driven, dumb render. Receives {type, id, rect} + optional
// chip-carried presentation fields. Resolves the entity record via byId() (DOM
// lookup of data-entity-id="{type}:{id}" + parse of data-ctx-fields), then
// derives rows via metaFor(). No per-type branching in this view.
//
// On-screen path: byId() finds the element → full data-ctx-fields row set.
//   No network call — instantaneous.
//
// Off-screen path (resolve-on-tap, §8):
//   byId() returns null (entity scrolled away / different tab) →
//   1. chipFallbackRecord() renders immediately (no blank state).
//   2. adapters.data.resolve() fires POST /api/mentions/resolve for full detail.
//   3. On response: re-render with the full data via metaFor().
//   4. On error/not_found: keep the fallback (graceful).
//   ~60s client cache keyed by "{type}:{ref}".
//
// Positioning: fixed, placed below the anchor rect when space allows, above
// otherwise. Width 268px with 8px margin from viewport edges.

import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { IdentityInner } from '../../../lucius/identity'
import type { IdentityItem } from '../../../lucius/identity'
import { colorFor, C } from '../../../lucius/mentionColors'
import { byId, metaFor, chipFallbackRecord } from '../../../lucius/objectFields'
import type { CtxFieldRecord } from '../../../lucius/objectFields'
import { labelFor } from '../../../lucius/entityRegistry'
import { getDataSource } from '../../../lucius/adapters'
import type { ResolvedEntity } from '../../../lucius/adapters'
import { sanitizeUntrusted } from '../../../lucius/security/fence'
import { isMaskableAmountOrQuantity, renderMaskableSublabel } from '../../../utils/mentionable'

// The shape of the detail object — all fields come from the chip's DOM dataset.
export interface MentionDetailData {
  type: string
  label: string
  /** Optional sublabel (e.g. ticker / account type / description). */
  sublabel?: string
  /** Optional date from the chip (transaction chips carry this). */
  chipDate?: string
  /** Optional signed amount from the chip (transaction chips carry this). */
  chipAmt?: string
  /** Optional value string (e.g. balance / price / amount). */
  value?: string
  /** Optional note shown after the type label ("· <note>"). */
  note?: string
  /** Color accent for the identity icon. */
  dot?: string
  /** Emoji override for the identity icon. */
  emoji?: string
  /** True for primary / Lucius agents (uses brand Spark). */
  primary?: boolean
  /** Entity id — used as the byId() lookup key. */
  id?: string
  /** Anchor rect (from getBoundingClientRect of the chip element). */
  rect: DOMRect
}

interface MentionDetailProps {
  detail: MentionDetailData | null
  onClose: () => void
}

const POPOVER_W = 268
const POPOVER_GAP = 8

// ── Resolve-on-tap cache (~60s TTL, cleared on conversation reset) ────────────
// Map key: "{type}:{ref}" → resolved entity data + timestamp.

interface CacheEntry {
  data: Record<string, string | number | null>
  ts: number
}

const _resolveCache = new Map<string, CacheEntry>()
const CACHE_TTL_MS = 60_000

function getCached(type: string, ref: string): Record<string, string | number | null> | null {
  const key = `${type}:${ref}`
  const entry = _resolveCache.get(key)
  if (!entry) return null
  if (Date.now() - entry.ts > CACHE_TTL_MS) {
    _resolveCache.delete(key)
    return null
  }
  return entry.data
}

function setCached(type: string, ref: string, data: Record<string, string | number | null>): void {
  _resolveCache.set(`${type}:${ref}`, { data, ts: Date.now() })
}

// ── Component ────────────────────────────────────────────────────────────────

export function MentionDetail({ detail, onClose }: MentionDetailProps) {
  // Close on outside click or Escape — mirrors the prototype's effect.
  useEffect(() => {
    if (!detail) return
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Element | null
      if (!t?.closest?.('.mx-detail') && !t?.closest?.('.chip')) onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [detail, onClose])

  // Resolved record — null until available (either from DOM or from the API).
  // Starts with the on-screen DOM record or the chip fallback; upgraded by
  // the resolve-on-tap fetch when the entity is off-screen.
  const [resolvedRecord, setResolvedRecord] = useState<CtxFieldRecord | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!detail) {
      setResolvedRecord(null)
      return
    }

    const entityId = detail.id ?? detail.type

    // 1. Try the DOM first (on-screen path) — no network, instantaneous.
    const domRecord = byId(detail.type, entityId)
    if (domRecord) {
      // data-ctx-fields never carries `id` (it's not a descriptor field, so the emitter
      // omits it), but the popup knows the ref. Inject it so objectFields renders the ID as
      // the last row — consistent with the off-screen fallback / cache / resolve paths below.
      setResolvedRecord(detail.id ? { ...domRecord, id: detail.id } : domRecord)
      return
    }

    // 2. Off-screen: fall back to chip-carried data immediately (no blank).
    const fallback = chipFallbackRecord({
      type: detail.type,
      label: detail.label,
      sublabel: detail.sublabel,
      chipDate: detail.chipDate,
      chipAmt: detail.chipAmt,
    })
    const fallbackWithId: CtxFieldRecord = detail.id ? { ...fallback, id: detail.id } : fallback
    setResolvedRecord(fallbackWithId)

    // 3. Check the ~60s client cache before firing a network request.
    const cached = getCached(detail.type, entityId)
    if (cached) {
      const cacheRecord: CtxFieldRecord = {}
      for (const [k, v] of Object.entries(cached)) {
        // Scalars only — a nested object would render as "[object Object]".
        if (v != null && typeof v !== 'object') cacheRecord[k] = String(v)
      }
      if (detail.id) cacheRecord['id'] = detail.id
      setResolvedRecord(cacheRecord)
      return
    }

    // 4. Fire resolve-on-tap via the DataSourceAdapter (POST /api/mentions/resolve).
    const dataSource = getDataSource()
    if (!dataSource) return

    const ac = new AbortController()
    abortRef.current = ac

    dataSource
      .resolve([{ type: detail.type, ref: entityId, label: detail.label }])
      .then((items: ResolvedEntity[]) => {
        if (ac.signal.aborted) return
        const item = items[0]
        if (!item || item.error || !item.data) return

        // Sanitize resolved values on INGRESS — before the cache, before the
        // popover render, before localStorage.  A server-side bug or a
        // no-backend adapter supplying hostile strings cannot forge fence
        // boundaries through the popover (RFC §14.7 #3, P0-A′, R-EX3).
        const sanitizedData: Record<string, string | number | null> = {}
        for (const [k, v] of Object.entries(item.data)) {
          if (v == null) {
            sanitizedData[k] = null
            continue
          }
          // Scalars only — a nested object/array (e.g. a spec dict a resolver forgot to
          // flatten) must never reach the cache or render as "[object Object]".
          if (typeof v === 'object') continue
          sanitizedData[k] = typeof v === 'string' ? sanitizeUntrusted(v, 200) : v
        }

        // Cache the sanitized result for subsequent taps.
        setCached(detail.type, entityId, sanitizedData)

        // Build a CtxFieldRecord from the sanitized data.
        const resolved: CtxFieldRecord = {}
        for (const [k, v] of Object.entries(sanitizedData)) {
          if (v != null) resolved[k] = String(v)
        }
        if (detail.id) resolved['id'] = detail.id
        setResolvedRecord(resolved)
      })
      .catch(() => {
        // Network/resolve error — keep the fallback record. Graceful degradation.
      })

    return () => {
      ac.abort()
    }
    // Reset and re-resolve on detail change (entity identity change).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.type, detail?.id, detail?.label])

  if (!detail) return null

  const entityId = detail.id ?? detail.type
  const record: CtxFieldRecord = resolvedRecord ?? {}

  const r = detail.rect
  const left = Math.min(Math.max(8, r.left), window.innerWidth - POPOVER_W - 8)
  const below = r.bottom + POPOVER_GAP
  const above = r.top - POPOVER_GAP
  const placeBelow = r.bottom + 220 < window.innerHeight
  const style: React.CSSProperties = placeBelow
    ? { position: 'fixed', width: POPOVER_W, top: below, left }
    : { position: 'fixed', width: POPOVER_W, bottom: window.innerHeight - above, left }

  // Derive popover rows: metaFor() filters objectFields to showInPopover !== false.
  const rows: [string, string][] = metaFor(record, detail.type)

  // Transaction icon tints by direction (red out / green in), matching the chip + picker.
  const txnAmt = record.amount ?? detail.chipAmt ?? ''
  const directionDot =
    detail.type === 'transaction' && txnAmt
      ? /^[−-]/.test(txnAmt.trim())
        ? C.neg
        : C.pos
      : detail.dot

  // Build identity item for the icon in the detail head.
  const identityItem: IdentityItem = {
    type: detail.type,
    label: detail.label,
    dot: directionDot,
    emoji: detail.emoji,
    primary: detail.primary,
    id: entityId,
  }

  const typeLabel = labelFor(detail.type)
  const iconColor = colorFor({ type: detail.type, dot: directionDot })

  // Portaled to document.body. The popover is position:fixed, but rendered inline it sits
  // inside the transcript — and .lx-side carries a transform, which makes IT the containing
  // block for any fixed descendant, so the popover was positioned/clipped against the panel
  // instead of the viewport and never appeared. dragRightClick's menu portals for the same
  // reason (see [data-lucius-portal] in lucius.css).
  return createPortal(
    <div className="mx-detail" data-lucius-portal="" style={{ ...style, zIndex: 'var(--z-modal)' }}>
      <div className="mx-detail-head">
        <span className="mx-detail-ic" style={{ color: iconColor }}>
          <IdentityInner item={identityItem} />
        </span>
        <div className="mx-detail-h">
          <div className="mx-detail-name">{detail.label}</div>
          <div className="mx-detail-type">
            {typeLabel}
            {detail.note ? (
              <>
                {' • '}
                {renderMaskableSublabel(detail.note)}
              </>
            ) : (
              ''
            )}
          </div>
        </div>
      </div>
      {rows.length > 0 && (
        <div className="mx-detail-rows">
          {rows.map(([k, v], i) => (
            <div className="mx-detail-row" key={i}>
              <span className="k">{k}</span>
              <span className={'v' + (isMaskableAmountOrQuantity(v) ? ' money' : '')}>{v}</span>
            </div>
          ))}
        </div>
      )}
    </div>,
    document.body,
  )
}
