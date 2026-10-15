// @vitest-environment jsdom
/**
 * EntityRow.test.tsx — unit + snapshot tests for the EntityRow / EntityList
 * primitives and the filterByDescriptor helper.
 *
 * Coverage (per task 1d):
 *   1. Emitted data-* == descriptor schema (only schema keys present).
 *   2. Sensitive + non-schema keys are excluded from data-ctx-fields.
 *   3. A row renders its children unchanged (transparent wrapper).
 *   4. EntityList emits data-ctx-region, data-ctx-title, data-ctx-fact.
 *   5. filterByDescriptor: unknown type → passthrough; sensitive key → dropped;
 *      non-schema key → dropped; known non-sensitive key → kept.
 *   6. Canonical attributes: data-entity-id, data-entity-label, data-ctx,
 *      data-ctx-type, data-ctx-fields, draggable are all present.
 *   7. `as` prop controls the host element tag.
 */

import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import React from 'react'
import { EntityRow } from './EntityRow'
import { EntityList } from './EntityList'
import {
  allDescriptors,
  filterByDescriptor,
  __test__setDescriptor,
  __test__deleteDescriptor,
} from './entityRegistry'

// ── Shared fixture ─────────────────────────────────────────────────────────────

// Use the real "account" descriptor from the bundled registry.
// Schema keys: name, kind, type, currency, balance, balance_base, cleared_balance_base
// None are sensitive in the current schema.
interface AccountItem {
  name: string
  kind: string
  type: string
  currency: string
  balance: string
  balance_base: string
  cleared_balance_base: string
  /** Not in schema — should be dropped. */
  internal_secret?: string
}

const ACCOUNT_ITEM: AccountItem = {
  name: 'Vantage Brokerage USD',
  kind: 'asset',
  type: 'Brokerage',
  currency: 'USD',
  balance: '$48,200.00',
  balance_base: 'S$65,000.00',
  cleared_balance_base: 'S$65,000.00',
  internal_secret: 'should_not_appear',
}

function accountToFields(item: AccountItem) {
  return {
    name: item.name,
    kind: item.kind,
    type: item.type,
    currency: item.currency,
    balance: item.balance,
    balance_base: item.balance_base,
    cleared_balance_base: item.cleared_balance_base,
    // non-schema key — must be dropped by filterByDescriptor
    internal_secret: item.internal_secret ?? null,
  }
}

// ── filterByDescriptor ─────────────────────────────────────────────────────────

describe('filterByDescriptor', () => {
  it('keeps only keys in the descriptor schema for a known type', () => {
    const desc = allDescriptors().find((d) => d.type === 'account')
    expect(desc).toBeDefined()

    const schemaKeys = new Set(desc!.fields.map((f) => f.key))
    const input = {
      name: 'Vantage Brokerage USD',
      balance: '$48,200.00',
      internal_secret: 'should_not_appear', // not in schema
      rogue_key: 'extra', // not in schema
    }
    const result = filterByDescriptor('account', input)

    // Schema keys present in input are kept.
    expect(result['name']).toBe('Vantage Brokerage USD')
    expect(result['balance']).toBe('$48,200.00')

    // Non-schema keys are dropped.
    expect('internal_secret' in result).toBe(false)
    expect('rogue_key' in result).toBe(false)

    // All result keys are in the schema.
    for (const key of Object.keys(result)) {
      expect(schemaKeys.has(key), `key "${key}" should be in the descriptor schema`).toBe(true)
    }
  })

  it('drops sensitive keys (defense-in-depth) — tested via synthetic descriptor', () => {
    // Inject a synthetic descriptor with one sensitive field and one non-sensitive field,
    // then assert that filterByDescriptor omits the sensitive field from its output.
    // The RFC (§5.2, §6) treats sensitive-omit-at-source as a security property — it
    // must have a passing test, not just a code path that is never exercised.
    const SYNTHETIC_TYPE = '__test_sensitive_type__'
    __test__setDescriptor({
      type: SYNTHETIC_TYPE,
      label: 'Test Type',
      icon: '◆',
      tone: 'neutral',
      fields: [
        {
          key: 'public_field',
          label: 'Public',
          type: 'text',
          sensitive: false,
          showInPopover: true,
        },
        {
          key: 'sensitive_field',
          label: 'Secret',
          type: 'text',
          sensitive: true,
          showInPopover: true,
        },
      ],
    })

    try {
      const result = filterByDescriptor(SYNTHETIC_TYPE, {
        public_field: 'hello',
        sensitive_field: 'top_secret',
      })
      // The non-sensitive schema key is kept.
      expect(result['public_field']).toBe('hello')
      // The sensitive schema key is dropped at source.
      expect('sensitive_field' in result).toBe(false)
    } finally {
      // Clean up the synthetic descriptor so it doesn't pollute other tests.
      __test__deleteDescriptor(SYNTHETIC_TYPE)
    }
  })

  it('returns input unchanged for an unknown type (no descriptor)', () => {
    const input = { foo: 'bar', baz: 42, secret: 'still_here' }
    const result = filterByDescriptor('__no_such_type__', input)
    expect(result).toEqual(input)
  })

  it('handles empty fields gracefully', () => {
    const result = filterByDescriptor('account', {})
    expect(result).toEqual({})
  })

  it('drops null and undefined values that survive into the filtered result', () => {
    // filterByDescriptor does NOT itself drop null/undefined — that's buildCtxFields' job.
    // But it still copies them if the key is in schema.
    const result = filterByDescriptor('account', { name: null, currency: undefined })
    // Both are schema keys for "account" → kept as-is (null/undefined).
    expect('name' in result).toBe(true)
    expect('currency' in result).toBe(true)
  })

  it('does NOT crash when a descriptor lacks a fields array (wire field_schema shape)', () => {
    // Regression: GET /api/entity-types ships the schema under `field_schema`; if a host
    // adapter forgets to map it to `fields`, refreshFromAdapter stores a descriptor with
    // `fields === undefined`.  filterByDescriptor must fall back to passthrough, never read
    // `.length` off undefined (which crashed every migrated tab: "Cannot read properties of
    // undefined (reading 'length')").
    const MISSHAPED = '__test_misshaped_no_fields__'
    __test__setDescriptor({
      type: MISSHAPED,
      label: 'Misshaped',
      icon: '◆',
      tone: 'neutral',
      // deliberately omit `fields` — cast simulates an unmapped wire descriptor
    } as unknown as Parameters<typeof __test__setDescriptor>[0])
    try {
      const input = { a: '1', b: 2 }
      expect(() => filterByDescriptor(MISSHAPED, input)).not.toThrow()
      expect(filterByDescriptor(MISSHAPED, input)).toEqual(input)
    } finally {
      __test__deleteDescriptor(MISSHAPED)
    }
  })
})

// ── EntityRow — data-* attributes ─────────────────────────────────────────────

describe('EntityRow — emitted data-* attributes', () => {
  function renderRow(overrides?: Partial<React.ComponentProps<typeof EntityRow<AccountItem>>>) {
    const { container } = render(
      <EntityRow
        type="account"
        id="acct-1"
        label="Vantage Brokerage USD"
        item={ACCOUNT_ITEM}
        toFields={accountToFields}
        search="Vantage Brokerage USD Brokerage"
        sublabel="Brokerage • USD"
        {...overrides}
      >
        <span>Cell content</span>
      </EntityRow>,
    )
    return container.firstElementChild as HTMLElement
  }

  it('emits data-entity-id as "{type}:{id}"', () => {
    const el = renderRow()
    expect(el.getAttribute('data-entity-id')).toBe('account:acct-1')
  })

  it('emits data-entity-label', () => {
    const el = renderRow()
    expect(el.getAttribute('data-entity-label')).toBe('Vantage Brokerage USD')
  })

  it('emits data-entity-search when provided', () => {
    const el = renderRow()
    expect(el.getAttribute('data-entity-search')).toBe('Vantage Brokerage USD Brokerage')
  })

  it('emits data-entity-sublabel when provided', () => {
    const el = renderRow()
    expect(el.getAttribute('data-entity-sublabel')).toBe('Brokerage • USD')
  })

  it('emits data-ctx=""', () => {
    const el = renderRow()
    expect(el.hasAttribute('data-ctx')).toBe(true)
    expect(el.getAttribute('data-ctx')).toBe('')
  })

  it('emits data-ctx-type', () => {
    const el = renderRow()
    expect(el.getAttribute('data-ctx-type')).toBe('account')
  })

  it('emits data-ctx-fields containing only descriptor schema keys', () => {
    const el = renderRow()
    const raw = el.getAttribute('data-ctx-fields')
    expect(raw).toBeTruthy()
    const parsed = JSON.parse(raw!)
    // Should have schema keys.
    expect(parsed['name']).toBe('Vantage Brokerage USD')
    expect(parsed['balance']).toBe('$48,200.00')
    // Non-schema key must be absent.
    expect('internal_secret' in parsed).toBe(false)
  })

  it('emits draggable=true by default', () => {
    const el = renderRow()
    expect(el.getAttribute('draggable')).toBe('true')
  })

  it('emits draggable=false when draggable prop is false', () => {
    const el = renderRow({ draggable: false })
    expect(el.getAttribute('draggable')).toBe('false')
  })

  it('stamps data-entity-selected when selected, absent otherwise', () => {
    expect(renderRow({ selected: true }).hasAttribute('data-entity-selected')).toBe(true)
    expect(renderRow().hasAttribute('data-entity-selected')).toBe(false)
    expect(renderRow({ selected: false }).hasAttribute('data-entity-selected')).toBe(false)
  })

  it('does NOT emit data-entity-search when search prop is absent', () => {
    const el = renderRow({ search: undefined })
    expect(el.hasAttribute('data-entity-search')).toBe(false)
  })

  it('does NOT emit data-entity-sublabel when sublabel prop is absent', () => {
    const el = renderRow({ sublabel: undefined })
    expect(el.hasAttribute('data-entity-sublabel')).toBe(false)
  })
})

// ── EntityRow — host-attribute pass-through (className/style/title/aria/data-*) ─

describe('EntityRow — host attribute pass-through', () => {
  function renderRow(overrides?: Partial<React.ComponentProps<typeof EntityRow<AccountItem>>>) {
    const { container } = render(
      <EntityRow
        type="account"
        id="acct-1"
        label="Vantage Brokerage USD"
        item={ACCOUNT_ITEM}
        toFields={accountToFields}
        {...overrides}
      >
        <span>Cell content</span>
      </EntityRow>,
    )
    return container.firstElementChild as HTMLElement
  }

  it('forwards className to the host element', () => {
    const el = renderRow({ className: 'row--clickable hl' })
    expect(el.classList.contains('row--clickable')).toBe(true)
    expect(el.classList.contains('hl')).toBe(true)
  })

  it('forwards style to the host element', () => {
    const el = renderRow({ style: { color: 'rgb(1, 2, 3)', marginTop: 4 } })
    expect(el.style.color).toBe('rgb(1, 2, 3)')
    expect(el.style.marginTop).toBe('4px')
  })

  it('forwards title to the host element', () => {
    const el = renderRow({ title: 'Hover me' })
    expect(el.getAttribute('title')).toBe('Hover me')
  })

  it('forwards arbitrary extra HTML attributes (aria-* / data-* / role) via ...rest', () => {
    const el = renderRow({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ...({
        'aria-label': 'Account row',
        'aria-selected': true,
        role: 'button',
        'data-testid': 'host-row',
        'data-custom-flag': 'on',
      } as any),
    })
    expect(el.getAttribute('aria-label')).toBe('Account row')
    expect(el.getAttribute('aria-selected')).toBe('true')
    expect(el.getAttribute('role')).toBe('button')
    expect(el.getAttribute('data-testid')).toBe('host-row')
    expect(el.getAttribute('data-custom-flag')).toBe('on')
  })

  it('a caller-supplied data-entity-id in rest does NOT override the engine value', () => {
    const el = renderRow({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ...({ 'data-entity-id': 'evil:hijacked' } as any),
    })
    // The engine's computed "{type}:{id}" wins — caller cannot clobber identity.
    expect(el.getAttribute('data-entity-id')).toBe('account:acct-1')
    expect(el.getAttribute('data-entity-id')).not.toBe('evil:hijacked')
  })

  it('a caller-supplied data-ctx-fields in rest does NOT override the engine value', () => {
    const el = renderRow({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ...({ 'data-ctx-fields': '{"injected":"value"}' } as any),
    })
    const raw = el.getAttribute('data-ctx-fields')!
    const parsed = JSON.parse(raw)
    // The engine's computed descriptor fields win, not the caller's stray prop.
    expect('injected' in parsed).toBe(false)
    expect(parsed['name']).toBe('Vantage Brokerage USD')
  })

  it('a caller-supplied data-ctx-type in rest does NOT override the engine value', () => {
    const el = renderRow({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ...({ 'data-ctx-type': 'spoofed_type' } as any),
    })
    expect(el.getAttribute('data-ctx-type')).toBe('account')
  })

  it('className/style/title pass-through coexists with all engine attributes', () => {
    const el = renderRow({ className: 'wrap', style: { opacity: 0.5 }, title: 'tip' })
    // Caller attrs present.
    expect(el.classList.contains('wrap')).toBe(true)
    expect(el.style.opacity).toBe('0.5')
    expect(el.getAttribute('title')).toBe('tip')
    // Engine attrs intact.
    expect(el.getAttribute('data-entity-id')).toBe('account:acct-1')
    expect(el.getAttribute('data-ctx-type')).toBe('account')
    expect(el.hasAttribute('data-ctx')).toBe(true)
    expect(el.getAttribute('draggable')).toBe('true')
  })
})

// ── EntityRow — children pass-through ─────────────────────────────────────────

describe('EntityRow — children pass-through (transparent wrapper)', () => {
  it('renders children inside the host element unchanged', () => {
    render(
      <EntityRow
        type="account"
        id="acct-2"
        label="Meridian Bank"
        item={ACCOUNT_ITEM}
        toFields={accountToFields}
      >
        <td data-testid="cell-a">Cell A</td>
        <td data-testid="cell-b">Cell B</td>
      </EntityRow>,
    )
    expect(screen.getByTestId('cell-a').textContent).toBe('Cell A')
    expect(screen.getByTestId('cell-b').textContent).toBe('Cell B')
  })

  it('does not inject any extra DOM nodes between the host and children', () => {
    const { container } = render(
      <EntityRow
        type="account"
        id="acct-3"
        label="Meridian Bank"
        item={ACCOUNT_ITEM}
        toFields={accountToFields}
        as="tr"
      >
        <td>only child</td>
      </EntityRow>,
    )
    const host = container.firstElementChild!
    expect(host.children).toHaveLength(1)
    expect(host.children[0]!.tagName).toBe('TD')
  })
})

// ── EntityRow — `as` prop ─────────────────────────────────────────────────────

describe('EntityRow — as prop controls host element', () => {
  it('defaults to div', () => {
    const { container } = render(
      <EntityRow type="account" id="x" label="x" item={ACCOUNT_ITEM} toFields={accountToFields}>
        <span />
      </EntityRow>,
    )
    expect(container.firstElementChild!.tagName).toBe('DIV')
  })

  it('renders as tr when as="tr"', () => {
    const { container } = render(
      <table>
        <tbody>
          <EntityRow
            type="account"
            id="x"
            label="x"
            item={ACCOUNT_ITEM}
            toFields={accountToFields}
            as="tr"
          >
            <td>cell</td>
          </EntityRow>
        </tbody>
      </table>,
    )
    const tr = container.querySelector('tr')!
    expect(tr.tagName).toBe('TR')
    expect(tr.getAttribute('data-entity-id')).toBe('account:x')
  })

  it('renders as li when as="li"', () => {
    const { container } = render(
      <ul>
        <EntityRow
          type="account"
          id="x"
          label="x"
          item={ACCOUNT_ITEM}
          toFields={accountToFields}
          as="li"
        >
          <span>item</span>
        </EntityRow>
      </ul>,
    )
    const li = container.querySelector('li')!
    expect(li.tagName).toBe('LI')
    expect(li.getAttribute('data-entity-id')).toBe('account:x')
  })
})

// ── EntityRow — hostRef forwarding ────────────────────────────────────────────

describe('EntityRow — hostRef forwarding', () => {
  it('forwards hostRef (callback ref) to the host DOM node', () => {
    let received: HTMLElement | null = null
    const { container } = render(
      <EntityRow
        type="account"
        id="acct-ref"
        label="Vantage Brokerage USD"
        item={ACCOUNT_ITEM}
        toFields={accountToFields}
        hostRef={(el) => {
          received = el
        }}
      >
        <span>cell</span>
      </EntityRow>,
    )
    // The callback ref received the actual rendered host element.
    expect(received).not.toBeNull()
    expect(received).toBe(container.firstElementChild)
  })

  it('forwards hostRef (object ref via useRef) to the host element with the right tag', () => {
    const captured = { current: null as HTMLElement | null }
    function Harness() {
      const ref = React.useRef<HTMLElement | null>(null)
      captured.current = ref.current
      return (
        <table>
          <tbody>
            <EntityRow
              type="account"
              id="acct-ref2"
              label="Vantage Brokerage USD"
              item={ACCOUNT_ITEM}
              toFields={accountToFields}
              as="tr"
              hostRef={(el) => {
                ref.current = el
                captured.current = el
              }}
            >
              <td>cell</td>
            </EntityRow>
          </tbody>
        </table>
      )
    }
    const { container } = render(<Harness />)
    const tr = container.querySelector('tr')!
    expect(tr.tagName).toBe('TR')
    // The ref target is the same element the host renders as.
    expect(captured.current).toBe(tr)
  })
})

// ── EntityRow — descriptor-schema filter for different types ──────────────────

describe('EntityRow — data-ctx-fields per descriptor schema', () => {
  it('emits only holding descriptor keys for type="holding"', () => {
    // Holding descriptor schema uses 'value_base' (not 'value') — the canonical
    // base-currency key.  'extra_field' is not in the schema and must be dropped.
    interface HoldingItem {
      symbol: string
      value_base: string
      extra_field: string
    }
    const item: HoldingItem = { symbol: 'AAPL', value_base: 'S$18,520', extra_field: 'ignored' }

    const { container } = render(
      <EntityRow
        type="holding"
        id="pos-1"
        label="AAPL"
        item={item}
        toFields={(i) => ({
          symbol: i.symbol,
          value_base: i.value_base,
          extra_field: i.extra_field,
        })}
      >
        <span />
      </EntityRow>,
    )
    const el = container.firstElementChild!
    const parsed = JSON.parse(el.getAttribute('data-ctx-fields')!)
    expect(parsed['symbol']).toBe('AAPL')
    expect(parsed['value_base']).toBe('S$18,520')
    // 'extra_field' is not in the holding descriptor — must be dropped.
    expect('extra_field' in parsed).toBe(false)
  })

  it('emits all descriptor schema keys that toFields returns (non-null)', () => {
    const desc = allDescriptors().find((d) => d.type === 'account')!
    const allSchemaKeys = desc.fields.map((f) => f.key)

    const fullItem: Record<string, string> = {}
    for (const k of allSchemaKeys) fullItem[k] = `value_of_${k}`

    const { container } = render(
      <EntityRow
        type="account"
        id="acct-full"
        label="Full"
        item={fullItem}
        toFields={(i) => i as Record<string, string>}
      >
        <span />
      </EntityRow>,
    )
    const el = container.firstElementChild!
    const parsed = JSON.parse(el.getAttribute('data-ctx-fields')!)
    for (const k of allSchemaKeys) {
      expect(parsed[k]).toBe(`value_of_${k}`)
    }
  })
})

// ── EntityRow — payee migration (PayeesPanel.tsx, RFC 1e) ─────────────────────

describe('EntityRow — payee row migration', () => {
  // Mirrors the exact toFields mapping PayeesPanel.tsx now emits for each payee row.
  // The payee descriptor schema keys are: name, default_categories, txn_count, hidden.
  // NOTE the plural. The 2026-07-20 payee lane retired the legacy SINGULAR
  // `default_category` scalar for per-kind `default_categories`, making it the sole source
  // of truth — the backend payee descriptor emits the plural. This test still asserted
  // the singular and had been red since; the descriptor was right, the test was stale.
  // Worth being explicit, because the first read of this failure was that `data-ctx-fields`
  // had silently DROPPED the field — which would have meant the model cannot see a payee's
  // default category on any screen rendering these rows. That would be a product bug. This
  // is a rename nobody propagated.
  // The old hand-written row emitted transactions/category/id/last_used — none of
  // which are in the schema, so they were dead in the DOM and must NOT appear here.
  interface PayeeItem {
    id: string
    name: string
    txn_count: number
    default_categories: string | null
    hidden: boolean
  }

  function renderPayee(item: PayeeItem) {
    const { container } = render(
      <EntityRow
        as="button"
        type="payee"
        id={item.id}
        label={item.name}
        item={item}
        toFields={(it) => ({
          name: it.name,
          txn_count: it.txn_count,
          default_categories: it.default_categories || null,
          hidden: it.hidden ? 'yes' : null,
        })}
      >
        <span className="payees-item-name">{item.name}</span>
        <span className="payees-item-count">{item.txn_count}</span>
      </EntityRow>,
    )
    return container.firstElementChild as HTMLElement
  }

  it('emits data-entity-id="payee:{id}" and data-ctx-type="payee"', () => {
    const el = renderPayee({
      id: 'p-1',
      name: 'Grab',
      txn_count: 42,
      default_categories: 'transport',
      hidden: false,
    })
    expect(el.getAttribute('data-entity-id')).toBe('payee:p-1')
    expect(el.getAttribute('data-ctx-type')).toBe('payee')
  })

  it('emits data-ctx-fields with descriptor keys name/txn_count/default_categories (not the old transactions/category)', () => {
    const el = renderPayee({
      id: 'p-1',
      name: 'Grab',
      txn_count: 42,
      default_categories: 'transport',
      hidden: false,
    })
    const parsed = JSON.parse(el.getAttribute('data-ctx-fields')!)
    // New, descriptor-aligned keys are present.
    expect(parsed['name']).toBe('Grab')
    expect(parsed['txn_count']).toBe(42)
    expect(parsed['default_categories']).toBe('transport')
    // Old, non-schema keys must be absent (the pre-migration bug they silently fixed).
    expect('transactions' in parsed).toBe(false)
    expect('category' in parsed).toBe(false)
    expect('id' in parsed).toBe(false)
    expect('last_used' in parsed).toBe(false)
  })

  it('drops null default_categories and the hidden flag when not set', () => {
    const el = renderPayee({
      id: 'p-2',
      name: 'Netflix',
      txn_count: 12,
      default_categories: null,
      hidden: false,
    })
    const parsed = JSON.parse(el.getAttribute('data-ctx-fields')!)
    expect(parsed['name']).toBe('Netflix')
    expect(parsed['txn_count']).toBe(12)
    expect('default_categories' in parsed).toBe(false)
    expect('hidden' in parsed).toBe(false)
  })

  it('renders as a native <button> so the row is keyboard-activated by construction', () => {
    const el = renderPayee({
      id: 'p-3',
      name: 'Spotify',
      txn_count: 3,
      default_categories: null,
      hidden: true,
    })
    expect(el.tagName).toBe('BUTTON')
  })
})

// ── EntityList ─────────────────────────────────────────────────────────────────

describe('EntityList — emitted data-ctx-* attributes', () => {
  it('emits data-ctx-region="content"', () => {
    const { container } = render(
      <EntityList title="Holdings" count={5}>
        <div>child</div>
      </EntityList>,
    )
    const el = container.firstElementChild!
    expect(el.getAttribute('data-ctx-region')).toBe('content')
  })

  it('emits data-ctx-title with the provided title', () => {
    const { container } = render(
      <EntityList title="Payees" count={12}>
        <div />
      </EntityList>,
    )
    expect(container.firstElementChild!.getAttribute('data-ctx-title')).toBe('Payees')
  })

  it('emits data-ctx-fact as "{title}: {count} items"', () => {
    const { container } = render(
      <EntityList title="Transactions" count={42}>
        <div />
      </EntityList>,
    )
    expect(container.firstElementChild!.getAttribute('data-ctx-fact')).toBe(
      'Transactions: 42 items',
    )
  })

  it('defaults to div host element', () => {
    const { container } = render(
      <EntityList title="Holdings" count={3}>
        <div />
      </EntityList>,
    )
    expect(container.firstElementChild!.tagName).toBe('DIV')
  })

  it('renders as tbody when as="tbody"', () => {
    const { container } = render(
      <table>
        <EntityList title="Holdings" count={1} as="tbody">
          <tr>
            <td>row</td>
          </tr>
        </EntityList>
      </table>,
    )
    const tbody = container.querySelector('tbody')!
    expect(tbody.tagName).toBe('TBODY')
    expect(tbody.getAttribute('data-ctx-region')).toBe('content')
    expect(tbody.getAttribute('data-ctx-title')).toBe('Holdings')
  })

  it('renders children unchanged', () => {
    render(
      <EntityList title="Payees" count={2}>
        <span data-testid="child-1">First</span>
        <span data-testid="child-2">Second</span>
      </EntityList>,
    )
    expect(screen.getByTestId('child-1').textContent).toBe('First')
    expect(screen.getByTestId('child-2').textContent).toBe('Second')
  })

  it('passes className through to the host element', () => {
    const { container } = render(
      <EntityList title="Accounts" count={0} className="my-class">
        <div />
      </EntityList>,
    )
    expect(container.firstElementChild!.classList.contains('my-class')).toBe(true)
  })
})

// ── Descriptor schema integrity (every backend type) ────────────────────────────────

describe('registered descriptors — field schema completeness', () => {
  it('every registered descriptor has at least one field', () => {
    for (const desc of allDescriptors()) {
      expect(desc.fields.length, `"${desc.type}" should have at least one field`).toBeGreaterThan(0)
    }
  })

  it('all field keys in each descriptor are unique', () => {
    for (const desc of allDescriptors()) {
      const keys = desc.fields.map((f) => f.key)
      const unique = new Set(keys)
      expect(unique.size, `"${desc.type}" has duplicate field keys`).toBe(keys.length)
    }
  })

  it('filterByDescriptor(type, allSchemaKeys) → all schema keys are kept', () => {
    for (const desc of allDescriptors()) {
      const input: Record<string, string> = {}
      for (const f of desc.fields) {
        if (!f.sensitive) input[f.key] = `v_${f.key}`
      }
      const result = filterByDescriptor(desc.type, input)
      for (const f of desc.fields) {
        if (!f.sensitive) {
          expect(result[f.key], `key "${f.key}" should be kept for type "${desc.type}"`).toBe(
            `v_${f.key}`,
          )
        }
      }
    }
  })
})
