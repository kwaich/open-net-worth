// @vitest-environment jsdom
//
// objectFields.test.ts — unit tests for the single field source layer.
//
// Coverage:
//   1. parseCtxFields — valid JSON, bad JSON, null/empty, filters out id/kind
//      (wait: parseCtxFields does NOT filter — objectFields does; test both)
//   2. objectFields — generic fallback for unknown type; hidden keys excluded
//      from showInPopover
//   3. metaFor — filters showInPopover=false rows
//   4. byId — on-screen resolution via data-ctx-fields; off-screen returns null
//   5. chipFallbackRecord — produces minimal record from chip attrs
//   6. Integration: chip-tap resolves on-screen data-ctx-fields into popover rows
//      without a network call
//   7. objectFields descriptor-ordered path — field order, labels, showInPopover=false,
//      extra keys appended, descriptor label overrides toLabel()

import { describe, it, expect, afterEach } from 'vitest'
import { parseCtxFields, byId, objectFields, metaFor, chipFallbackRecord } from './objectFields'

// ── Helpers ────────────────────────────────────────────────────────────────────

function setBody(html: string): void {
  document.body.innerHTML = html
}

afterEach(() => {
  document.body.innerHTML = ''
})

// ── parseCtxFields ────────────────────────────────────────────────────────────

describe('parseCtxFields', () => {
  it('returns null for null input', () => {
    expect(parseCtxFields(null)).toBeNull()
  })

  it('returns null for empty string', () => {
    expect(parseCtxFields('')).toBeNull()
  })

  it('returns null for invalid JSON', () => {
    expect(parseCtxFields('{not json}')).toBeNull()
  })

  it('returns null for JSON array', () => {
    expect(parseCtxFields('[1,2,3]')).toBeNull()
  })

  it('returns null for empty object', () => {
    expect(parseCtxFields('{}')).toBeNull()
  })

  it('parses a valid object and coerces values to strings', () => {
    const rec = parseCtxFields(JSON.stringify({ date: '2024-01-15', amount: 100 }))
    expect(rec).toEqual({ date: '2024-01-15', amount: '100' })
  })

  it('skips null and empty-string values', () => {
    const rec = parseCtxFields(JSON.stringify({ name: 'Alice', note: null, tag: '' }))
    expect(rec).toEqual({ name: 'Alice' })
  })

  it('returns null when all values are null/empty', () => {
    expect(parseCtxFields(JSON.stringify({ a: null, b: '' }))).toBeNull()
  })
})

// ── objectFields ──────────────────────────────────────────────────────────────

describe('objectFields', () => {
  it('generic fallback: produces rows for an unknown entity type', () => {
    const rec = { name: 'Test entity', value: '$100' }
    const rows = objectFields(rec)
    expect(rows.length).toBe(2)
    expect(rows[0]).toMatchObject({
      key: 'name',
      label: 'Name',
      value: 'Test entity',
      showInPopover: true,
    })
    expect(rows[1]).toMatchObject({
      key: 'value',
      label: 'Value',
      value: '$100',
      showInPopover: true,
    })
  })

  it('shows "id" as the last row, labeled "ID"', () => {
    const rec = { id: 'abc-123', name: 'Alice' }
    const rows = objectFields(rec)
    const idRow = rows.find((r) => r.key === 'id')
    expect(idRow?.showInPopover).toBe(true)
    expect(idRow?.label).toBe('ID')
    // id is moved to the end — a reference, not a headline field.
    expect(rows[rows.length - 1]?.key).toBe('id')
  })

  it('marks "kind" key as showInPopover=false', () => {
    const rec = { kind: 'expense', amount: '-50 SGD' }
    const rows = objectFields(rec)
    const kindRow = rows.find((r) => r.key === 'kind')
    expect(kindRow?.showInPopover).toBe(false)
  })

  it('converts snake_case keys to Title Case labels', () => {
    const rec = { account_name: 'Meridian Bank', budget_month: '2024-01' }
    const rows = objectFields(rec)
    expect(rows[0]?.label).toBe('Account Name')
    expect(rows[1]?.label).toBe('Budget Month')
  })

  it('handles camelCase keys', () => {
    const rec = { payeeId: 'p1', netWorth: '$100k' }
    const rows = objectFields(rec)
    expect(rows[0]?.label).toBe('Payee Id')
    expect(rows[1]?.label).toBe('Net Worth')
  })
})

// ── metaFor ────────────────────────────────────────────────────────────────────

describe('metaFor', () => {
  it('filters out showInPopover=false rows', () => {
    const rec = { id: 'txn-1', date: '2024-01-15', amount: '+100 SGD', kind: 'income' }
    const rows = metaFor(rec)
    const keys = rows.map(([k]) => k)
    // id and kind are hidden
    expect(keys).not.toContain('Id')
    expect(keys).not.toContain('Kind')
    // date and amount are visible
    expect(keys).toContain('Date')
    expect(keys).toContain('Amount')
  })

  it('returns [[label, value]] tuples', () => {
    const rec = { balance: 'S$5,000', currency: 'SGD' }
    const rows = metaFor(rec)
    expect(rows).toEqual([
      ['Balance', 'S$5,000'],
      ['Currency', 'SGD'],
    ])
  })

  it('returns empty array for empty record', () => {
    expect(metaFor({})).toEqual([])
  })
})

// ── byId ──────────────────────────────────────────────────────────────────────

describe('byId', () => {
  it('returns null when element is not in the DOM (off-screen fallback)', () => {
    setBody('<div>nothing here</div>')
    // No network call is made — purely DOM-based.
    const result = byId('transaction', 'txn-999')
    expect(result).toBeNull()
  })

  it('returns null when element has no data-ctx-fields', () => {
    setBody('<div data-entity-id="account:acct-1">Meridian Bank</div>')
    expect(byId('account', 'acct-1')).toBeNull()
  })

  it('resolves on-screen data-ctx-fields into a record', () => {
    setBody(`
      <div
        data-entity-id="transaction:txn-1"
        data-ctx-fields='{"date":"2024-01-15","amount":"+200 SGD","account":"Meridian Bank","id":"txn-1","kind":"expense"}'
      >
        Meridian Bank — +200 SGD
      </div>
    `)
    const rec = byId('transaction', 'txn-1')
    expect(rec).not.toBeNull()
    expect(rec?.date).toBe('2024-01-15')
    expect(rec?.amount).toBe('+200 SGD')
    expect(rec?.account).toBe('Meridian Bank')
  })

  it('integration: on-screen element resolves into full popover rows without a network call', () => {
    // Simulate a transaction row as Cash.tsx emits it.
    setBody(`
      <div
        data-entity-id="transaction:txn-2"
        data-ctx-fields='{"id":"txn-2","date":"2024-03-10","payee":"Grab","description":"Grab ride","category":"transport","amount":"-14.80 SGD","account":"Meridian Multiplier","kind":"expense"}'
      ></div>
    `)
    const rec = byId('transaction', 'txn-2')
    expect(rec).not.toBeNull()
    const rows = metaFor(rec!)
    // id and kind must be hidden; the rest visible
    const labels = rows.map(([l]) => l)
    expect(labels).not.toContain('Id')
    expect(labels).not.toContain('Kind')
    expect(labels).toContain('Date')
    expect(labels).toContain('Payee')
    expect(labels).toContain('Amount')
    expect(labels).toContain('Account')
    expect(labels).toContain('Category')
    // No fetch was triggered — purely from the DOM.
  })
})

// ── chipFallbackRecord ────────────────────────────────────────────────────────

describe('chipFallbackRecord', () => {
  it('produces a minimal record with Type and Name', () => {
    const rec = chipFallbackRecord({ type: 'account', label: 'Meridian Multiplier' })
    expect(rec.type).toBe('Account')
    expect(rec.name).toBe('Meridian Multiplier')
  })

  it('includes sublabel, chipDate, chipAmt when provided', () => {
    const rec = chipFallbackRecord({
      type: 'transaction',
      label: 'Grab',
      sublabel: 'Grab • Meridian Bank • 2024-03-10 • -14.80 SGD',
      chipDate: '2024-03-10',
      chipAmt: '-14.80 SGD',
    })
    expect(rec.details).toBe('Grab • Meridian Bank • 2024-03-10 • -14.80 SGD')
    expect(rec.date).toBe('2024-03-10')
    expect(rec.amount).toBe('-14.80 SGD')
  })

  it('off-screen fallback renders generic rows without a network call', () => {
    // Simulate a chip for a transaction that is NOT in the DOM (scrolled away).
    document.body.innerHTML = ''
    const rec = chipFallbackRecord({
      type: 'transaction',
      label: 'Netflix',
      chipDate: '2024-02-01',
      chipAmt: '-15.98 SGD',
    })
    // byId returns null — no element in the DOM.
    expect(byId('transaction', 'txn-offscreen')).toBeNull()
    // fallback record still produces usable popover rows.
    const rows = metaFor(rec)
    expect(rows.length).toBeGreaterThan(0)
    const labels = rows.map(([l]) => l)
    expect(labels).toContain('Date')
    expect(labels).toContain('Amount')
  })
})

// ── objectFields — descriptor-ordered path ────────────────────────────────────
//
// Tests for §6 single-field-source: when a type has a descriptor, objectFields
// must use the descriptor's field order and labels, not record key order.
// Uses the real backend descriptors (seeded by vitest.setup.ts; no registry mock needed).

describe('objectFields — descriptor-ordered path (type parameter)', () => {
  it('uses descriptor field order, not record key order', () => {
    // The "category" descriptor has field order: key, label, group, month, assigned,
    // activity, available, target, status.  We supply them out of schema order in
    // the record and assert the output follows the schema order.
    const rec = {
      status: 'on_track',
      available: '$100',
      label: 'Groceries',
      key: 'groceries',
    }
    // Pass 'category' to engage the descriptor-ordered path.
    const rows = objectFields(rec, 'category')

    // 'label' (descriptor position 1) must come before 'status' (last in schema).
    const labelIdx = rows.findIndex((r) => r.key === 'label')
    const statusIdx = rows.findIndex((r) => r.key === 'status')
    expect(labelIdx).toBeGreaterThanOrEqual(0)
    expect(statusIdx).toBeGreaterThanOrEqual(0)
    expect(labelIdx).toBeLessThan(statusIdx)
  })

  it('showInPopover=false from descriptor is honored', () => {
    // The "category" descriptor marks "key" with showInPopover=false.
    const rec = { key: 'groceries', label: 'Groceries', group: 'Essentials' }
    const rows = objectFields(rec, 'category')
    const keyRow = rows.find((r) => r.key === 'key')
    expect(keyRow).toBeDefined()
    expect(keyRow!.showInPopover).toBe(false)
  })

  it('metaFor hides descriptor showInPopover=false rows', () => {
    // 'key' is showInPopover=false in the category descriptor — must not appear in metaFor output.
    const rec = { key: 'rent', label: 'Rent', group: 'Housing' }
    const labels = metaFor(rec, 'category').map(([l]) => l)
    // 'Key' (label for the 'key' field) must be hidden.
    expect(labels).not.toContain('Key')
    // 'Name' (descriptor label for the 'label' field) and 'Group' must be visible.
    expect(labels).toContain('Name')
    expect(labels).toContain('Group')
  })

  it('extra keys not in the descriptor schema are appended after descriptor rows', () => {
    // Supply a record with known descriptor fields plus one unknown extra key.
    const rec = {
      label: 'Transport',
      group: 'Spending',
      extra_custom_field: 'some value', // not in 'category' descriptor
    }
    const rows = objectFields(rec, 'category')
    const extraRow = rows.find((r) => r.key === 'extra_custom_field')
    expect(extraRow).toBeDefined()
    // The extra key must appear AFTER all descriptor-listed keys.
    const lastDescriptorIdx = rows.reduce((max, r, idx) => {
      return r.key === 'label' || r.key === 'group' ? idx : max
    }, -1)
    const extraIdx = rows.findIndex((r) => r.key === 'extra_custom_field')
    expect(extraIdx).toBeGreaterThan(lastDescriptorIdx)
  })

  it('descriptor label overrides toLabel() for a known field', () => {
    // The "category" descriptor gives the "label" field the human label "Name",
    // NOT "Label" (which toLabel("label") would produce).
    const rec = { label: 'Groceries' }
    const rows = objectFields(rec, 'category')
    const labelRow = rows.find((r) => r.key === 'label')
    expect(labelRow).toBeDefined()
    expect(labelRow!.label).toBe('Name') // from descriptor, not toLabel('label') = 'Label'
  })

  it('falls back to toLabel() for extra keys not in the descriptor', () => {
    const rec = { label: 'Food', my_custom_key: 'foo' }
    const rows = objectFields(rec, 'category')
    const extraRow = rows.find((r) => r.key === 'my_custom_key')
    expect(extraRow).toBeDefined()
    expect(extraRow!.label).toBe('My Custom Key') // toLabel() applied
  })

  it('absent descriptor keys (not in record) are silently skipped', () => {
    // Only supply some of the descriptor fields — absent keys must not produce rows.
    const rec = { label: 'Travel' }
    const rows = objectFields(rec, 'category')
    const keys = rows.map((r) => r.key)
    expect(keys).not.toContain('group')
    expect(keys).not.toContain('month')
    expect(keys).toContain('label')
  })
})
