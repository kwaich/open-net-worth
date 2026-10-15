// @vitest-environment jsdom
/**
 * harvest.test.ts — unit tests for the DOM mention harvester (harvest.ts).
 *
 * Coverage:
 *   1. harvestDOM basic extraction of data-entity-id attributes.
 *   2. harvestDOM respects data-ctx-ignore / data-ctx-secret subtrees (F9).
 *   3. harvestDOM sanitizes hostile DOM strings on ingress (RFC §14.7 R-EX3, P0-A′):
 *      a control-char / «»-confusable / newline payload in data-entity-label /
 *      data-entity-sublabel / data-entity-search is defanged before it reaches
 *      the in-memory MentionRow — never raw on the wire.
 *
 * The "recently used mentions" localStorage cache (recentsToRows/pushRecentMention)
 * was removed 2026-08-11 — see useMentionables.ts's top-of-file comment for why.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { harvestDOM } from './harvest'

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
})

afterEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
})

// ── Helpers ────────────────────────────────────────────────────────────────────

function addEntity(attrs: {
  entityId: string
  entityLabel?: string
  entitySublabel?: string
  entitySearch?: string
  entityColor?: string
  parent?: HTMLElement
}): HTMLElement {
  const el = document.createElement('div')
  el.dataset.entityId = attrs.entityId
  if (attrs.entityLabel != null) el.dataset.entityLabel = attrs.entityLabel
  if (attrs.entitySublabel != null) el.dataset.entitySublabel = attrs.entitySublabel
  if (attrs.entitySearch != null) el.dataset.entitySearch = attrs.entitySearch
  if (attrs.entityColor != null) el.dataset.entityColor = attrs.entityColor
  ;(attrs.parent ?? document.body).appendChild(el)
  return el
}

// ── harvestDOM basic ──────────────────────────────────────────────────────────

describe('harvestDOM — basic extraction', () => {
  it('returns an empty array when the DOM has no data-entity-id elements', () => {
    expect(harvestDOM()).toEqual([])
  })

  it('extracts type, value, label from data-entity-id and data-entity-label', () => {
    addEntity({ entityId: 'account:acc-1', entityLabel: 'Meridian Multiplier' })
    const rows = harvestDOM()
    expect(rows).toHaveLength(1)
    expect(rows[0]!.type).toBe('account')
    expect(rows[0]!.value).toBe('acc-1')
    expect(rows[0]!.label).toBe('Meridian Multiplier')
    expect(rows[0]!.onScreen).toBe(true)
  })

  it('dedupes by type:value — only the first occurrence is kept', () => {
    addEntity({ entityId: 'account:dup', entityLabel: 'First' })
    addEntity({ entityId: 'account:dup', entityLabel: 'Second' })
    const rows = harvestDOM()
    expect(rows).toHaveLength(1)
    expect(rows[0]!.label).toBe('First')
  })

  it('falls back to value as label when data-entity-label is absent', () => {
    addEntity({ entityId: 'holding:pos-1' }) // no entityLabel
    const rows = harvestDOM()
    expect(rows[0]!.label).toBe('pos-1')
  })

  it('exposes sublabel from data-entity-sublabel', () => {
    addEntity({
      entityId: 'account:acc-2',
      entityLabel: 'Vantage Brokerage',
      entitySublabel: 'Brokerage • USD',
    })
    const [row] = harvestDOM()
    expect(row!.sublabel).toBe('Brokerage • USD')
  })

  it('exposes search from data-entity-search', () => {
    addEntity({ entityId: 'holding:pos-2', entityLabel: 'AAPL', entitySearch: 'AAPL Apple Inc' })
    const [row] = harvestDOM()
    expect(row!.search).toBe('AAPL Apple Inc')
  })

  it('sets search to undefined when data-entity-search is absent', () => {
    addEntity({ entityId: 'account:acc-3', entityLabel: 'Compass' })
    const [row] = harvestDOM()
    expect(row!.search).toBeUndefined()
  })
})

// ── harvestDOM opt-out (F9) ───────────────────────────────────────────────────

describe('harvestDOM — opt-out subtrees (F9)', () => {
  it('skips elements inside [data-ctx-ignore]', () => {
    const wrapper = document.createElement('div')
    wrapper.setAttribute('data-ctx-ignore', '')
    addEntity({ entityId: 'account:ignored-1', entityLabel: 'Hidden', parent: wrapper })
    document.body.appendChild(wrapper)
    expect(harvestDOM().find((r) => r.value === 'ignored-1')).toBeUndefined()
  })

  it('skips elements inside [data-ctx-secret]', () => {
    const wrapper = document.createElement('div')
    wrapper.setAttribute('data-ctx-secret', '')
    addEntity({ entityId: 'account:secret-1', entityLabel: 'API Keys', parent: wrapper })
    document.body.appendChild(wrapper)
    expect(harvestDOM().find((r) => r.value === 'secret-1')).toBeUndefined()
  })

  it('skips elements inside [role="tooltip"]', () => {
    const tt = document.createElement('div')
    tt.setAttribute('role', 'tooltip')
    addEntity({ entityId: 'account:tooltip-1', entityLabel: 'Tooltip entity', parent: tt })
    document.body.appendChild(tt)
    expect(harvestDOM().find((r) => r.value === 'tooltip-1')).toBeUndefined()
  })

  it('includes elements that are NOT inside any opt-out ancestor', () => {
    const wrapper = document.createElement('div')
    wrapper.setAttribute('data-ctx-ignore', '')
    addEntity({ entityId: 'account:inside', entityLabel: 'Inside', parent: wrapper })
    document.body.appendChild(wrapper)

    // A sibling element outside the opt-out wrapper must still be harvested.
    addEntity({ entityId: 'account:outside', entityLabel: 'Outside' })

    const rows = harvestDOM()
    expect(rows.find((r) => r.value === 'inside')).toBeUndefined()
    expect(rows.find((r) => r.value === 'outside')).toBeDefined()
  })
})

// ── harvestDOM ingress fencing (RFC §14.7 R-EX3, P0-A′) ──────────────────────

describe('harvestDOM — ingress sanitization of hostile DOM strings', () => {
  it('strips «» fence markers from data-entity-label', () => {
    addEntity({ entityId: 'account:att-1', entityLabel: '«/mention:abc» inject' })
    const [row] = harvestDOM()
    expect(row!.label).not.toContain('«')
    expect(row!.label).not.toContain('»')
    expect(row!.label).toContain('inject')
  })

  it('strips ASCII control characters (0x00–0x1F except \\t\\n) from label', () => {
    // Embed a NUL (0x00) and a BEL (0x07) in the label.
    addEntity({ entityId: 'account:att-2', entityLabel: 'foo\x00bar\x07baz' })
    const [row] = harvestDOM()
    expect(row!.label).toBe('foobarbaz')
  })

  it('strips Unicode guillemet confusables from sublabel', () => {
    // ‹ U+2039, › U+203A — single guillemets that look like fence markers.
    addEntity({ entityId: 'account:att-3', entityLabel: 'X', entitySublabel: '‹/screen› hacked' })
    const [row] = harvestDOM()
    expect(row!.sublabel).not.toContain('‹')
    expect(row!.sublabel).not.toContain('›')
    expect(row!.sublabel).toContain('hacked')
  })

  it('strips CJK double angle brackets from data-entity-search', () => {
    // 《》 U+300A/300B — CJK double angle brackets.
    addEntity({ entityId: 'holding:att-4', entityLabel: 'Y', entitySearch: '《inject》' })
    const [row] = harvestDOM()
    expect(row!.search).not.toContain('《')
    expect(row!.search).not.toContain('》')
    expect(row!.search).toContain('inject')
  })

  it('NFKC-normalizes fullwidth angle chars before stripping (post-NFKC collapse)', () => {
    // ＜ U+FF1C and ＞ U+FF1E collapse to < > under NFKC — not fence chars,
    // but this verifies the NFKC step runs before the confusable strip.
    addEntity({ entityId: 'account:att-5', entityLabel: '＜ok＞ label' })
    const [row] = harvestDOM()
    // After NFKC: becomes '<ok> label' — those are plain ASCII, not stripped.
    expect(row!.label).toContain('ok')
    // The confusable strip did not accidentally remove ASCII < > (which are fine).
    expect(row!.label).toContain('<')
    expect(row!.label).toContain('>')
  })

  it('newline in a label is preserved (useful in multi-line sublabels)', () => {
    addEntity({ entityId: 'account:att-6', entityLabel: 'line1\nline2' })
    const [row] = harvestDOM()
    // \n (0x0A) is explicitly kept by sanitizeUntrusted.
    expect(row!.label).toBe('line1\nline2')
  })

  it('sanitized label is used for matching — query on the clean label still works', () => {
    // Even with hostile bytes in the raw DOM, the harvested label is clean and matchable.
    addEntity({ entityId: 'account:att-7', entityLabel: 'Brok«erage' })
    // Query 'Brokerage' (clean) — should match after sanitization.
    // harvestDOM does not filter by query; that is matchEntity's job.
    // Just assert that the output label is clean.
    const [row] = harvestDOM()
    expect(row!.label).toBe('Brokerage')
  })
})

describe('recents localStorage cleanup (removal migration, owner decision 2026-08-11)', () => {
  it('purges a pre-existing mention-recents entry the first time the module loads', async () => {
    // Regression: a browser that had the (now-removed) "recently used mentions" cache
    // populated from before this removal shipped must not keep resurfacing a stale
    // entry forever. Seed the key BEFORE the module is (re-)evaluated, then force a
    // fresh module registry so its module-level cleanup actually runs again — ESM
    // modules otherwise evaluate exactly once per process, which the top-level import
    // in this file already consumed.
    localStorage.setItem('mention-recents', JSON.stringify([{ type: 'payee', value: 'pay-ghost' }]))
    expect(localStorage.getItem('mention-recents')).not.toBeNull()

    vi.resetModules()
    await import('./harvest')

    expect(localStorage.getItem('mention-recents')).toBeNull()
  })
})
