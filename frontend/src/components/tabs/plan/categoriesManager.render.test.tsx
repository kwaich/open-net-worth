// @vitest-environment jsdom
//
// Rendering coverage for CategoriesManager's three fix-lane changes: ENV-RES-03 (onMutated
// fires the live Budget table's own refresh, not just the manager's local reload), ENV-RES-06
// ("Add to envelope…" per unlinked, non-income row), and CONF-6 (the Envelope-column tooltip
// resolves member keys to LABELS, not raw store keys).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { CategoriesManager } from './categoriesManager'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}

const CATEGORIES = [
  {
    id: 'groceries',
    label: 'Groceries',
    kind: 'expense',
    color: '#3b86dd',
    hidden: false,
  },
  {
    id: 'dining',
    label: 'Dining',
    kind: 'expense',
    color: '#e0a032',
    hidden: false,
  },
  {
    id: 'annual_gift',
    label: 'Annual gift',
    kind: 'expense',
    color: '#d4537e',
    hidden: false,
  },
  {
    id: 'salary',
    label: 'Salary',
    kind: 'income',
    color: '#1d9e75',
    hidden: false,
  },
]
// "groceries" and "dining" are both linked to env-groceries (a merged envelope, deliberately
// named something OTHER than either member so its own label never collides with a category
// row's own label text in the DOM); "annual_gift" and "salary" stay unlinked.
const ENVELOPES = [
  {
    id: 'env-groceries',
    name: 'Food budget',
    color: '#3b86dd',
    category_ids: ['groceries', 'dining'],
  },
]

function mockRoutes() {
  mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
    const method = opts?.method ?? 'GET'
    if (url === '/api/categories?include_hidden=true') return Promise.resolve(okJson(CATEGORIES))
    if (url === '/api/planning/envelopes') return Promise.resolve(okJson(ENVELOPES))
    if (/^\/api\/planning\/envelopes\/[^/]+\/add-category$/.test(url) && method === 'POST')
      return Promise.resolve(okJson({ ok: true }))
    return Promise.resolve(okJson({}))
  })
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  mockRoutes()
})
afterEach(() => cleanup())

async function mounted(onMutated = vi.fn(), toast = vi.fn()) {
  const rendered = render(
    <CategoriesManager toast={toast} onClose={vi.fn()} onMutated={onMutated} />,
  )
  await waitFor(() => expect(screen.getByText('Groceries')).toBeTruthy())
  return rendered
}

describe('CategoriesManager — CONF-6: Envelope-column tooltip resolves member keys to labels', () => {
  it('the merged Food-budget row\'s tooltip lists LABELS ("Groceries, Dining"), not raw keys', async () => {
    await mounted()
    const envCell = screen.getAllByText('Food budget')[0]!.closest('.pl-catmgr-env')
    expect(envCell?.getAttribute('title')).toBe('Groceries, Dining')
  })
})

describe('CategoriesManager — category taxability removed', () => {
  it('renders no taxable column or category tax pills', async () => {
    await mounted()
    expect(screen.queryByText('Taxable')).toBeNull()
    expect(screen.queryByText('Tax-free')).toBeNull()
  })
})

describe('CategoriesManager — ENV-RES-06: "Add to envelope…" per unlinked row', () => {
  it('offers "Add to envelope…" on an unlinked EXPENSE category', async () => {
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: 'Annual gift options' }))
    expect(screen.getByText('Add to envelope…')).toBeTruthy()
  })

  it('does NOT offer it on an already-linked category', async () => {
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: 'Groceries options' }))
    expect(screen.queryByText('Add to envelope…')).toBeNull()
  })

  it('does NOT offer it on an income-kind category (envelopes are spending-only)', async () => {
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: 'Salary options' }))
    expect(screen.queryByText('Add to envelope…')).toBeNull()
  })

  it('picking a target links the category, reloads, and fires onMutated (ENV-RES-03)', async () => {
    const onMutated = vi.fn()
    const toast = vi.fn()
    await mounted(onMutated, toast)
    fireEvent.click(screen.getByRole('button', { name: 'Annual gift options' }))
    fireEvent.click(screen.getByText('Add to envelope…'))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'env-groceries' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add to envelope' }))
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/planning/envelopes/env-groceries/add-category',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ category_id: 'annual_gift' }),
        }),
      ),
    )
    await waitFor(() => expect(onMutated).toHaveBeenCalled())
    // The picker's own target label decorates an N>1 envelope with its member count
    // (allEnvelopeTargets, budgetEnvelopeOps.tsx) — same convention every other envelope-target
    // picker in this lane uses, so the toast inherits it verbatim.
    expect(toast).toHaveBeenCalledWith('Annual gift now draws from Food budget (2 categories)')
  })
})

describe('CategoriesManager — ENV-RES-03: every mutating write invalidates the live Budget model too', () => {
  it("renaming a category fires onMutated alongside the manager's own reload", async () => {
    mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
      const method = opts?.method ?? 'GET'
      if (url === '/api/categories?include_hidden=true') return Promise.resolve(okJson(CATEGORIES))
      if (url === '/api/planning/envelopes') return Promise.resolve(okJson(ENVELOPES))
      if (url === '/api/categories/dining' && method === 'PATCH')
        return Promise.resolve(okJson({ ok: true }))
      return Promise.resolve(okJson({}))
    })
    const onMutated = vi.fn()
    await mounted(onMutated)
    fireEvent.doubleClick(screen.getByText('Dining'))
    const input = screen.getByDisplayValue('Dining')
    fireEvent.change(input, { target: { value: 'Eating out' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(onMutated).toHaveBeenCalled())
  })
})
