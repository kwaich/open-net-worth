// @vitest-environment jsdom
//
// Task 176 — below the phone breakpoint the transaction drawer renders as a BOTTOM SHEET,
// not a right-docked drawer. A 384px panel on a 390px viewport left a sliver of register
// beside it with every touch target pinned into the far corners ("too hard to interact").
//
// What's pinned here is the JS half of the split (the CSS half lives in accounts.css's own
// 640px block, gated on the SAME breakpoint): the `sheet` class both halves key off, no
// inline width, no pointer-only resize handle, the grab handle + its 40px drag-to-dismiss,
// and that the gesture mirrors the ✕ (from Edit it returns to View, never discards).

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Category, Txn } from './types'
import { SCROLL_LOCK_TIMEOUT_MS } from '../../../hooks/useSheetDragToDismiss'

afterEach(cleanup)

function makeAccount(over: Partial<Account> = {}): Account {
  return {
    id: 'a1',
    name: 'Northgate Rewards Card',
    kind: 'asset',
    type: 'credit',
    institution: 'Northgate',
    currency: 'SGD',
    initialBalance: 0,
    initialBalanceDate: null,
    creditLimit: null,
    lastFour: '1234',
    note: '',
    statementCloseDay: null,
    paymentDueDay: null,
    reminderDays: null,
    lastReconciled: null,
    archived: false,
    connected: false,
    apr: null,
    principal: null,
    payoffDate: null,
    minPayment: null,
    apy: null,
    balance: -100,
    balanceBase: -100,
    clearedBalance: -100,
    clearedBalanceBase: -100,
    role: 'cash',
    holdingsBase: 0,
    ...over,
  }
}

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-07-26',
    kind: 'expense',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'METAPAY*MV4B',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 26.15,
    fromCurrency: 'SGD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 26.15,
    baseCurrency: 'SGD',
    cleared: true,
    source: 'manual',
    createdAt: '2026-07-26',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

/** Forces useIsMobile() (jsdom's default matchMedia stub reports desktop — see vitest.setup.ts).
 *  `globalThis.matchMedia` is a getter-only accessor there, so the property must be redefined
 *  rather than assigned (same helper as Register.mobileDateGroups.test.tsx). */
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

const activity = Promise.resolve({
  source: 'manual',
  addedBy: 'You',
  createdAt: '2026-07-26',
  updatedAt: '2026-07-26',
  activity: [],
})

function renderDrawer(onClose: () => void = () => {}) {
  return render(
    <TxnDetail
      txn={makeTxn()}
      accounts={[makeAccount()]}
      categories={[] as Category[]}
      payeeOptions={[]}
      baseCurrency="SGD"
      rates={{}}
      onClose={onClose}
      onSave={() => {}}
      onDelete={() => {}}
      onMarkReviewed={() => {}}
      onManagePayees={() => {}}
      onCreateCategory={() => {}}
      onLoadActivity={() => activity}
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
}

/** Simulates a pointer sequence targeting the sheet's own scrollable content directly — for
 * `.acct-detail`, which is ITSELF the scrollable container (overflow-y: auto, not a wrapper
 * around a separate `-body`), that is the root element. Unlike the grab bar / kicker row
 * (opted-in drag surfaces), a touch here is subject to the scroll position. */
async function dragContent(dy: number) {
  const sheet = document.querySelector('.acct-detail.sheet') as HTMLElement
  Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })
  fireEvent.pointerDown(sheet, { clientY: 200, pointerId: 1 })
  await new Promise((resolve) => setTimeout(resolve, 150))
  fireEvent.pointerMove(sheet, { clientY: 200 + dy, pointerId: 1 })
  fireEvent.pointerUp(sheet, { clientY: 200 + dy, pointerId: 1 })
}

/** A tap (or a drag too short to cross the catch threshold): never registers as a drag at all. */
function tapHandle() {
  const handle = document.querySelector('.acct-detail-grab') as HTMLElement
  fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
  fireEvent.pointerUp(handle, { clientY: 200, pointerId: 1 })
}

/** `.acct-detail` (sheetRef) is unlike `.mb-sheet`/`.kebab-sheet`: it's ITSELF the scrollable
 * container (overflow-y: auto), not a wrapper around a separate `-body`. Simulates it already
 * scrolled down, as jsdom does no real layout (same technique as
 * useSheetDragToDismiss.test.tsx's own makeScrollable). */
function scrollSheetDown(scrollTop: number) {
  const sheet = document.querySelector('.acct-detail.sheet') as HTMLElement
  Object.defineProperty(sheet, 'scrollHeight', { value: 1200, configurable: true })
  Object.defineProperty(sheet, 'clientHeight', { value: 400, configurable: true })
  sheet.style.overflowY = 'auto'
  sheet.scrollTop = scrollTop
}

/** Simulates useSheetDragToDismiss's live drag on the dedicated grab handle — the ONLY drag
 * target now: pointerdown, a real pause (so the trailing-window velocity calc reads ~0 and only
 * the drag DISTANCE governs the commit decision — see the hook's VELOCITY_WINDOW_MS), then one
 * pointermove of `dy` px and a release. jsdom does no layout, so the sheet's offsetHeight is
 * mocked to a realistic px value the distance ratio can compare against (real setPointerCapture
 * is also unavailable in jsdom — the hook calls it optionally). */
async function dragHandle(dy: number) {
  const sheet = document.querySelector('.acct-detail.sheet') as HTMLElement
  const handle = document.querySelector('.acct-detail-grab') as HTMLElement
  Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })
  fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
  await new Promise((resolve) => setTimeout(resolve, 150))
  fireEvent.pointerMove(handle, { clientY: 200 + dy, pointerId: 1 })
  fireEvent.pointerUp(handle, { clientY: 200 + dy, pointerId: 1 })
}

/** Unlike `mockMobile` above, `prefers-reduced-motion` reads false here (only the max-width
 * query matches `isMobile`) — needed to exercise the REAL 200ms exit-timer window a committed
 * drag's release animation runs in, which `mockMobile`'s "every query matches" stub collapses
 * to a synchronous call (prefers-reduced-motion also reads true whenever isMobile is true). */
function mockMobileRealTiming(isMobile: boolean) {
  const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const fn = ((query: string) => ({
    matches: query.includes('max-width') ? isMobile : false,
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

describe('TxnDetail — bottom sheet below 640px', () => {
  it('renders as a sheet: sheet class on wrap + panel, no inline width, no resize handle', () => {
    const restore = mockMobile(true)
    try {
      renderDrawer()
      const wrap = document.querySelector('.acct-detail-wrap') as HTMLElement
      const panel = document.querySelector('.acct-detail') as HTMLElement
      expect(wrap.classList.contains('sheet')).toBe(true)
      expect(panel.classList.contains('sheet')).toBe(true)
      // The CSS block sizes the sheet — an inline width would need !important to beat.
      expect(panel.style.width).toBe('')
      // A 7px col-resize strip is a pointer-only affordance and a touch trap.
      expect(document.querySelector('.acct-detail-resize')).toBeNull()
      expect(document.querySelector('.acct-detail-grab')).toBeTruthy()
    } finally {
      restore()
    }
  })

  it('a downward drag on the handle past the distance threshold dismisses it', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      renderDrawer(onClose)
      await dragHandle(200)
      expect(onClose).toHaveBeenCalledTimes(1)
    } finally {
      restore()
    }
  })

  it('a short drag on the handle (or a tap) does NOT dismiss it', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      renderDrawer(onClose)
      await dragHandle(30)
      tapHandle()
      expect(onClose).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it('dragging the content directly (not scrolled — no room to absorb it) dismisses it too, same as the handle', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      renderDrawer(onClose)
      // `.acct-detail` reports 0 for scrollHeight/clientHeight by default in jsdom (no real
      // layout) — nothing to scroll, so the whole pull goes straight to the sheet, exactly
      // like dragging the handle does.
      await dragContent(200)
      expect(onClose).toHaveBeenCalledTimes(1)
    } finally {
      restore()
    }
  })

  it('in Edit mode a committed drag on the handle returns to View instead of discarding the edit', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      renderDrawer(onClose)
      fireEvent.click(screen.getByText('Edit transaction'))
      expect(screen.getByText('Save changes')).toBeTruthy()
      await dragHandle(200)
      expect(onClose).not.toHaveBeenCalled()
      expect(screen.getByText('Edit transaction')).toBeTruthy() // back in View mode
    } finally {
      restore()
    }
  })

  it('desktop is unchanged: no sheet class, inline width, resize handle, no grab handle', () => {
    const restore = mockMobile(false)
    try {
      renderDrawer()
      const wrap = document.querySelector('.acct-detail-wrap') as HTMLElement
      const panel = document.querySelector('.acct-detail') as HTMLElement
      expect(wrap.classList.contains('sheet')).toBe(false)
      expect(panel.classList.contains('sheet')).toBe(false)
      expect(panel.style.width).toBe('384px')
      expect(document.querySelector('.acct-detail-resize')).toBeTruthy()
      expect(document.querySelector('.acct-detail-grab')).toBeNull()
    } finally {
      restore()
    }
  })

  it('when scrolled down, a drag on the handle still dismisses — the sticky chrome opts in as a drag surface', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      renderDrawer(onClose)
      // `.acct-detail` is both the sheet root AND its own scrollable body — the handle and the
      // kicker/✕ row sit INSIDE it (position: sticky, not siblings), so without the opt-in a
      // handle drag would resolve the scrolled root and be declined as the browser's scroll.
      scrollSheetDown(300)
      const sheet = document.querySelector('.acct-detail.sheet') as HTMLElement
      const top = document.querySelector('.acct-detail-top') as HTMLElement
      expect(
        document.querySelector('.acct-detail-grab')!.hasAttribute('data-sheet-drag-surface'),
      ).toBe(true)
      expect(top.hasAttribute('data-sheet-drag-surface')).toBe(true)
      await dragHandle(200)
      expect(sheet.scrollTop).toBe(300) // unchanged — the hook never writes scrollTop
      expect(onClose).toHaveBeenCalledTimes(1)
    } finally {
      restore()
    }
  })

  it('a scrolled gesture over the CONTENT never becomes a dismiss, even after it reaches its top — the NEXT pull does', async () => {
    const restore = mockMobile(true)
    try {
      const onClose = vi.fn()
      renderDrawer(onClose)
      scrollSheetDown(60)
      const sheet = document.querySelector('.acct-detail.sheet') as HTMLElement
      await dragContent(400)
      expect(onClose).not.toHaveBeenCalled()
      expect(sheet.style.transform).toBe('') // never armed
      expect(sheet.scrollTop).toBe(60) // never touched — the hook never writes scrollTop

      // The browser (not the hook) scrolls the content to its top under the same finger, then
      // settles past SCROLL_LOCK_TIMEOUT_MS before the next gesture starts.
      sheet.scrollTop = 0
      await new Promise((resolve) => setTimeout(resolve, SCROLL_LOCK_TIMEOUT_MS + 50))

      await dragContent(200)
      expect(onClose).toHaveBeenCalledTimes(1)
    } finally {
      restore()
    }
  })

  it('a stale exit-timer does not close the drawer for a txn switched to mid-animation', async () => {
    const restore = mockMobileRealTiming(true)
    try {
      const onClose = vi.fn()
      const txnA = makeTxn({ id: 't1' })
      const txnB = makeTxn({ id: 't2' })
      const props = {
        accounts: [makeAccount()],
        categories: [] as Category[],
        payeeOptions: [],
        baseCurrency: 'SGD',
        rates: {},
        onClose,
        onSave: () => {},
        onDelete: () => {},
        onMarkReviewed: () => {},
        onManagePayees: () => {},
        onCreateCategory: () => {},
        onLoadActivity: () => activity,
        onLoadSourceEntries: () => Promise.resolve(null),
        onDetachSourceRef: () => Promise.resolve(),
      }
      const { rerender } = render(<TxnDetail txn={txnA} {...props} />)
      fireEvent.click(screen.getByText('Edit transaction'))
      expect(screen.getByText('Save changes')).toBeTruthy()

      // Commits a drag-dismiss (on the handle — the only drag target) while mode === 'edit' —
      // the gesture intends to flip back to View (mirroring ✕), never to close the drawer
      // outright. Real (non-reduced-motion) timing: the exit animation, and its onDismiss call,
      // are still 200ms away.
      const sheet = document.querySelector('.acct-detail.sheet') as HTMLElement
      const handle = document.querySelector('.acct-detail-grab') as HTMLElement
      Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })
      fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(handle, { clientY: 400, pointerId: 1 })
      fireEvent.pointerUp(handle, { clientY: 400, pointerId: 1 })

      // Before the exit timer fires, the parent swaps to a different transaction — same
      // TxnDetail instance, but its own `useEffect(() => setMode('view'), [txn?.id])` resets
      // mode back to View for the new txn.
      rerender(<TxnDetail txn={txnB} {...props} />)

      await new Promise((resolve) => setTimeout(resolve, 250)) // exit timer fires

      expect(onClose).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })
})
