// @vitest-environment jsdom
//
// Regression coverage for the mobile "Manage categories" fix: below 640px, CategoriesManager
// swaps CatMgrRow's fixed-width grid for CatMgrCard's stacked layout (Register.tsx's TxnRow →
// TxnCard pattern), so the modal never needs horizontal scroll on a phone. The actual bug was
// `.pl-catmgr-modal .pl-table-inner { min-width: 640px }` forcing a scrollbar under that width —
// this test asserts the DOM shape that avoids it: no `.pl-thead` grid header and no `.pl-catmgr-row`
// grid rows are rendered on mobile, only `.pl-catmgr-card`s (the CSS then drops `.pl-table-inner`'s
// min-width to 0 for the same breakpoint — see plan.css's `@media (max-width: 640px)` block).
//
// Queries go through `document.body`, not the `render()` result's own `container`: PlanningModal
// renders via a Radix `Dialog.Portal`, which mounts outside RTL's container div entirely.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react'
import { CategoriesManager } from './categoriesManager'
import { CatMgrCard } from './CatMgrCard'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}

const CATEGORIES = [
  { id: 'groceries', label: 'Groceries', kind: 'expense', color: '#3b86dd', hidden: false },
  { id: 'salary', label: 'Salary', kind: 'income', color: '#1d9e75', hidden: false },
]

/** Forces useIsMobile() (jsdom's default matchMedia stub reports desktop — see vitest.setup.ts).
 *  `globalThis.matchMedia` is installed as a getter-only accessor by vitest.setup.ts, so a plain
 *  `window.matchMedia = ...` assignment silently no-ops — must redefine the property instead. */
function mockMobile(isMobile: boolean) {
  const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const fn = ((query: string) => ({
    matches: isMobile,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
  return () => {
    if (original) Object.defineProperty(window, 'matchMedia', original)
  }
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation((url: string) => {
    if (url === '/api/categories?include_hidden=true') return Promise.resolve(okJson(CATEGORIES))
    if (url === '/api/planning/envelopes') return Promise.resolve(okJson([]))
    return Promise.resolve(okJson({}))
  })
})
afterEach(() => cleanup())

describe('CategoriesManager — mobile card layout (no horizontal-scroll grid below 640px)', () => {
  it('renders CatMgrCard rows, not the grid header, when useIsMobile() is true', async () => {
    const restore = mockMobile(true)
    try {
      render(<CategoriesManager toast={vi.fn()} onClose={vi.fn()} onMutated={vi.fn()} />)
      await waitFor(() => expect(screen.getByText('Groceries')).toBeTruthy())
      expect(document.body.querySelector('.pl-thead')).toBeNull()
      expect(document.body.querySelector('.pl-catmgr-row')).toBeNull()
      expect(document.body.querySelectorAll('.pl-catmgr-card').length).toBe(2)
      expect(screen.queryByText('Taxable')).toBeNull()
      expect(screen.queryByText('Tax-free')).toBeNull()
      // The standalone "Hidden" toggle row was removed (redundant with the kebab's own
      // Hide/Unhide item) — the card face renders no "Hidden" text and no `.pl-tgt-switch`.
      expect(screen.queryByText('Hidden')).toBeNull()
      expect(document.body.querySelector('.pl-tgt-switch')).toBeNull()
    } finally {
      restore()
    }
  })

  it("reaches Hide only via the card's kebab menu, and it fires the real toggle-hide POST", async () => {
    const restore = mockMobile(true)
    try {
      render(<CategoriesManager toast={vi.fn()} onClose={vi.fn()} onMutated={vi.fn()} />)
      await waitFor(() => expect(screen.getByText('Groceries')).toBeTruthy())

      fireEvent.click(screen.getByRole('button', { name: 'Groceries options' }))
      fireEvent.click(screen.getByText('Hide'))

      await waitFor(() =>
        expect(mockAuthFetch).toHaveBeenCalledWith(
          '/api/categories/groceries/hide',
          expect.objectContaining({ method: 'POST' }),
        ),
      )
    } finally {
      restore()
    }
  })

  it('keeps the desktop grid (CatMgrRow + pl-thead) when useIsMobile() is false', async () => {
    const restore = mockMobile(false)
    try {
      render(<CategoriesManager toast={vi.fn()} onClose={vi.fn()} onMutated={vi.fn()} />)
      await waitFor(() => expect(screen.getByText('Groceries')).toBeTruthy())
      expect(document.body.querySelector('.pl-thead')).not.toBeNull()
      expect(document.body.querySelectorAll('.pl-catmgr-row').length).toBe(2)
      expect(document.body.querySelector('.pl-catmgr-card')).toBeNull()
    } finally {
      restore()
    }
  })
})

// CategoriesManager itself filters every STRUCTURAL_CATEGORY_KEYS row out of `all` before it
// ever reaches CatMgrRow/CatMgrCard (the `split` sentinel isn't a user category — see
// categoriesManager.tsx's own comment on that filter), so the locked-category branch can only
// be exercised by rendering the card directly — same approach lucius.entityWiring.test.tsx
// already uses for CatMgrRow.
describe('CatMgrCard — locked/structural category', () => {
  const lockedCat = { id: 'split', label: 'Split', kind: 'expense', color: '#888888' }
  const rowProps = {
    envelope: undefined,
    labelOf: (k: string) => k,
    renaming: false,
    onStartRename: () => {},
    onCancelRename: () => {},
    onRename: () => {},
    onRecolor: () => {},
    onToggleHide: () => {},
    onDelete: () => {},
  }

  it('renders with no kebab menu and no way to toggle hidden at all', () => {
    const { container } = render(<CatMgrCard c={lockedCat} {...rowProps} />)
    // The kebab wrapper is present but hidden/inert (visibility:hidden, pointer-events:none,
    // tabIndex -1) rather than omitted — same DOM shape CatMgrRow uses for a locked row. With
    // the standalone toggle removed, hide/unhide lives ONLY in that kebab, so a locked category
    // now has no `.pl-tgt-switch` fallback either — it simply cannot be hidden from this card.
    const kebabWrap = container.querySelector('.pl-kebab-wrap')
    expect(kebabWrap).not.toBeNull()
    expect(kebabWrap?.getAttribute('aria-hidden')).toBe('true')
    expect(container.querySelector('.pl-kebab[tabindex="-1"]')).not.toBeNull()
    expect(container.querySelector('.pl-tgt-switch')).toBeNull()
  })
})
