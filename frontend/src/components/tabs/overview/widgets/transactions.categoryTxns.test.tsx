// @vitest-environment jsdom
//
// "Category transactions" lists recent transactions in the categories an instance is set
// to, by category id (so renaming a category never unhooks it). The Overview's
// "Subscriptions" card is this widget, preset to the starter Subscriptions category.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

import { transactionsWidgetComponents } from './transactions'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'
import { seedConfig } from '../grid'

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

// Serves a book's categories and an empty transactions page for everything else.
function serve(categories: { id: string; label: string }[]) {
  authFetch.mockImplementation((url: string) =>
    jsonOk(url.startsWith('/api/categories') ? categories : { transactions: [] }),
  )
}

const txnCalls = () =>
  authFetch.mock.calls.map((c) => String(c[0])).filter((u) => u.startsWith('/api/transactions'))

function renderWidget(config: Record<string, unknown>) {
  const Comp = transactionsWidgetComponents['category-txns']!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'category-txns')!
  const instance: WidgetInstance = {
    instanceId: `w-${Math.random()}`,
    defId: 'category-txns',
    x: 0,
    y: 0,
    w: 4,
    h: 240,
    config,
  }
  return render(<Comp instance={instance} def={def} onPatch={vi.fn()} />)
}

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

describe('Category transactions widget', () => {
  it('asks for every picked category by id', async () => {
    serve([
      { id: 'cat_aaa', label: 'Groceries' },
      { id: 'cat_bbb', label: 'Dining out' },
    ])
    renderWidget({ categories: ['cat_aaa', 'cat_bbb'] })
    await waitFor(() =>
      expect(authFetch.mock.calls.map((c) => c[0])).toContain(
        '/api/transactions?category=cat_aaa&category=cat_bbb&limit=10',
      ),
    )
  })

  it('asks the user to pick categories instead of fetching everything', async () => {
    serve([{ id: 'cat_aaa', label: 'Groceries' }])
    renderWidget({ categories: [] })
    expect(await screen.findByText(/Pick the categories to list/)).toBeTruthy()
    expect(txnCalls()).toEqual([])
  })
})

describe('Subscriptions widget', () => {
  it('is the category widget preset to the starter Subscriptions category', async () => {
    serve([{ id: 'cat_f2ff09940000', label: 'Subscriptions' }])
    const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'subscriptions')!
    expect(def.title).toBe('Subscriptions')
    const Comp = transactionsWidgetComponents.subscriptions!
    const instance: WidgetInstance = {
      instanceId: 'w-subs',
      defId: 'subscriptions',
      x: 0,
      y: 0,
      w: 4,
      h: 240,
      config: seedConfig(def, undefined),
    }
    render(<Comp instance={instance} def={def} onPatch={vi.fn()} />)
    await waitFor(() =>
      expect(authFetch.mock.calls.map((c) => c[0])).toContain(
        '/api/transactions?category=cat_f2ff09940000&limit=10',
      ),
    )
  })

  it("finds a book's own Subscriptions category when the starter one isn't there", async () => {
    // An imported or older book: its "Subscriptions" has an id the preset can't know.
    serve([
      { id: 'cat_c90eefa2f203', label: 'Subscriptions' },
      { id: 'cat_aaa', label: 'Groceries' },
    ])
    const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'subscriptions')!
    const Comp = transactionsWidgetComponents.subscriptions!
    const instance: WidgetInstance = {
      instanceId: 'w-subs-own',
      defId: 'subscriptions',
      x: 0,
      y: 0,
      w: 4,
      h: 240,
      config: seedConfig(def, undefined),
    }
    render(<Comp instance={instance} def={def} onPatch={vi.fn()} />)
    await waitFor(() =>
      expect(txnCalls()).toContain('/api/transactions?category=cat_c90eefa2f203&limit=10'),
    )
  })
})

describe('Open register from a category widget', () => {
  it('opens the register filtered to the categories the card lists', async () => {
    serve([{ id: 'cat_c90eefa2f203', label: 'Subscriptions' }])
    const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'subscriptions')!
    const Comp = transactionsWidgetComponents.subscriptions!
    const onOpenRegister = vi.fn()
    const instance: WidgetInstance = {
      instanceId: 'w-subs-link',
      defId: 'subscriptions',
      x: 0,
      y: 0,
      w: 4,
      h: 240,
      config: seedConfig(def, undefined),
    }
    render(
      <Comp
        instance={instance}
        def={def}
        onPatch={vi.fn()}
        onNavigate={vi.fn()}
        onOpenRegister={onOpenRegister}
      />,
    )
    const link = await screen.findByRole('button', { name: /Open register/ })
    await waitFor(() => expect(txnCalls().length).toBeGreaterThan(0))
    fireEvent.click(link)
    expect(onOpenRegister).toHaveBeenCalledWith({ category: ['cat_c90eefa2f203'] })
  })
})
