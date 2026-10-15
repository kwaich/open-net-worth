// @vitest-environment jsdom
//
// Mobile card layout (phone, useIsMobile()) — the Budget sub-tab's fixed 4-column `.pl-trow`
// grid forces horizontal scroll below ~640px (owner-caught, screenshots at 390px). CategoryRow /
// GroupBlock / OutsideEnvelopes each grow a card-based mobile branch (budgetCards.tsx shells)
// that reuses every leaf control (ColorDot/Kebab/AvailCell/ActivityCell/EnvCatsBadge/the target
// bar) verbatim — only the JSX wrapper differs. Mirrors budget.zeroEnvelope.test.tsx's
// render-CategoryRow-directly convention; mocks `useIsMobile` the same way
// Register.mobileDateGroups.test.tsx does (jsdom's matchMedia stub always reports desktop).
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { CategoryRow, GroupBlock, OutsideEnvelopes } from './budget'
import type { OutsideEnvelopesData } from './budget'
import type { LocalCat, LocalGroup } from './useBudgetModel'
import { fSmart, fFig } from './planData'

/** Forces useIsMobile() (jsdom's default matchMedia stub reports desktop — see vitest.setup.ts).
 *  `globalThis.matchMedia` is installed as a getter-only accessor by vitest.setup.ts, so a plain
 *  `window.matchMedia = ...` assignment silently no-ops — must redefine the property instead. */
function mockMobile(isMobile: boolean) {
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
}

afterEach(() => cleanup())
beforeEach(() => mockMobile(true))

function mkCat(
  overrides: Partial<LocalCat> & Pick<LocalCat, 'key' | 'label' | 'envelopeId' | 'categoryIds'>,
): LocalCat {
  return {
    color: '#34b08a',
    assigned: 0,
    activity: 0,
    available: 0,
    target: 0,
    tt: null,
    cad: null,
    status: 'none',
    needed: 0,
    ...overrides,
  }
}

function noopDnd() {
  return {
    catStart: () => () => {},
    groupStart: () => () => {},
    rowOver: () => () => {},
    rowDrop: () => () => {},
    groupOver: () => () => {},
    groupDrop: () => () => {},
    end: () => {},
  }
}

function renderRow(c: LocalCat, allGroups: LocalGroup[] = []) {
  return render(
    <CategoryRow
      c={c}
      gid="g-a"
      dnd={noopDnd()}
      over={null}
      otherGroups={[]}
      locked={false}
      month="2026-07"
      allGroups={allGroups}
      catLabel={(k) => k}
      catColor={() => '#34b08a'}
      rta={0}
      onOpenCommitment={vi.fn()}
      onOpenCardPayment={vi.fn()}
      onAssign={vi.fn()}
      onAction={vi.fn()}
      onRename={vi.fn()}
      onRecolor={vi.fn()}
      onMoveTo={vi.fn()}
      onToggleHide={vi.fn()}
      onDeleteEnvelope={vi.fn()}
      onEditTarget={vi.fn()}
      onTransfer={vi.fn()}
      onMerge={vi.fn()}
      onSplit={vi.fn()}
      onAddCats={vi.fn()}
    />,
  )
}

describe('CategoryRow — mobile card layout (useIsMobile forced true)', () => {
  it('renders as a .pl-mcard, never inside the desktop .pl-trow/.pl-crow grid', () => {
    const c = mkCat({
      key: 'dining',
      label: 'Dining',
      envelopeId: 'env-dining',
      categoryIds: ['dining'],
      assigned: 120,
      activity: -45,
      available: 75,
    })
    const { container } = renderRow(c)
    expect(container.querySelector('.pl-mcard')).toBeTruthy()
    expect(container.querySelector('.pl-trow')).toBeNull()
    expect(container.querySelector('.pl-crow')).toBeNull()
  })

  it('renders the ASSIGNED/ACTIVITY/AVAILABLE labels and figures in a 3-up grid', () => {
    const c = mkCat({
      key: 'dining',
      label: 'Dining',
      envelopeId: 'env-dining',
      categoryIds: ['dining'],
      assigned: 120,
      activity: -45,
      available: 75,
    })
    const { container } = renderRow(c)
    expect(container.querySelector('.pl-mcard-nums')).toBeTruthy()
    expect(screen.getByText('ASSIGNED')).toBeTruthy()
    expect(screen.getByText('ACTIVITY')).toBeTruthy()
    expect(screen.getByText('AVAILABLE')).toBeTruthy()
    expect(screen.getByText('Dining')).toBeTruthy()
  })

  it('a Credit-Card-Payments row labels its third column PAYMENT, not AVAILABLE', () => {
    const c = mkCat({
      key: 'cc',
      label: 'Visa Payment',
      envelopeId: 'env-cc',
      categoryIds: [],
      isPaymentEnvelope: true,
      availableForPayment: 40,
      paymentUnderfunded: 0,
    })
    renderRow(c)
    expect(screen.getByText('PAYMENT')).toBeTruthy()
    expect(screen.queryByText('AVAILABLE')).toBeNull()
  })

  it('the options Kebab is present and reachable (not the locked hidden placeholder)', () => {
    const c = mkCat({
      key: 'dining',
      label: 'Dining',
      envelopeId: 'env-dining',
      categoryIds: ['dining'],
    })
    renderRow(c)
    const kebab = screen.getByLabelText('Dining options')
    expect(kebab).toBeTruthy()
    // the locked-row placeholder is inline-styled `visibility: hidden` — a real Kebab carries
    // no such inline hiding style, so shipping the placeholder by mistake is directly testable.
    expect(kebab.closest('.pl-kebab-wrap')?.getAttribute('style') ?? '').not.toMatch(
      /visibility:\s*hidden/,
    )
  })

  it('the Cover/Fund/Move action button renders in normal flow (no hover-only desktop class alone)', () => {
    const c = mkCat({
      key: 'dining',
      label: 'Dining',
      envelopeId: 'env-dining',
      categoryIds: ['dining'],
      assigned: 30,
      target: 100,
      status: 'underfunded',
      needed: 70,
    })
    render(
      <CategoryRow
        c={c}
        gid="g-a"
        dnd={noopDnd()}
        over={null}
        otherGroups={[]}
        locked={false}
        month="2026-07"
        allGroups={[]}
        catLabel={(k) => k}
        catColor={() => '#34b08a'}
        rta={0}
        onOpenCommitment={vi.fn()}
        onOpenCardPayment={vi.fn()}
        onAssign={vi.fn()}
        onAction={vi.fn()}
        onRename={vi.fn()}
        onRecolor={vi.fn()}
        onMoveTo={vi.fn()}
        onToggleHide={vi.fn()}
        onDeleteEnvelope={vi.fn()}
        onEditTarget={vi.fn()}
        onTransfer={vi.fn()}
        onMerge={vi.fn()}
        onSplit={vi.fn()}
        onAddCats={vi.fn()}
      />,
    )
    const btn = screen.getByRole('button', { name: /Fund/ })
    expect(btn.className).toMatch(/pl-caction--mobile/)
  })

  it('hides the redundant status line when a Cover/Fund/Move action button is present', () => {
    const c = mkCat({
      key: 'dining',
      label: 'Dining',
      envelopeId: 'env-dining',
      categoryIds: ['dining'],
      assigned: 30,
      target: 100,
      status: 'underfunded',
      needed: 70,
    })
    const { container } = renderRow(c)
    expect(screen.getByRole('button', { name: /Fund/ })).toBeTruthy()
    expect(container.querySelector('.pl-mcard-status')).toBeNull()
  })

  it('still shows "Funded" when there is no action button to duplicate it', () => {
    const c = mkCat({
      key: 'dining',
      label: 'Dining',
      envelopeId: 'env-dining',
      categoryIds: ['dining'],
      assigned: 100,
      target: 100,
      status: 'funded',
      needed: 0,
    })
    const { container } = renderRow(c)
    expect(screen.queryByRole('button', { name: /Cover|Fund|Move/ })).toBeNull()
    expect(container.querySelector('.pl-mcard-status')?.textContent).toBe('Funded')
  })

  it('has no drag grip (drag-to-reorder is desktop-only)', () => {
    const c = mkCat({
      key: 'dining',
      label: 'Dining',
      envelopeId: 'env-dining',
      categoryIds: ['dining'],
    })
    const { container } = renderRow(c)
    expect(container.querySelector('.pl-grip')).toBeNull()
  })

  it('renders byte-identical desktop markup once useIsMobile reports false (control)', () => {
    mockMobile(false)
    const c = mkCat({
      key: 'dining',
      label: 'Dining',
      envelopeId: 'env-dining',
      categoryIds: ['dining'],
    })
    const { container } = renderRow(c)
    expect(container.querySelector('.pl-trow.pl-crow')).toBeTruthy()
    expect(container.querySelector('.pl-mcard')).toBeNull()
  })

  it('places assigned/activity/available in their OWN slot of the 3-up grid, never swapped', () => {
    const c = mkCat({
      key: 'dining',
      label: 'Dining',
      envelopeId: 'env-dining',
      categoryIds: ['dining'],
      assigned: 120,
      activity: -45,
      available: 75,
    })
    const { container } = renderRow(c)
    const nums = container.querySelectorAll('.pl-mnum')
    expect(nums).toHaveLength(3)
    expect(nums[0]!.textContent).toContain(fSmart(120))
    expect(nums[1]!.textContent).toContain(fFig(-45).text)
    expect(nums[2]!.textContent).toContain(fFig(75).text)
  })

  it("a payment envelope's overpaid state renders NO status text on mobile either", () => {
    const c = mkCat({
      key: 'cc',
      label: 'Visa Payment',
      envelopeId: 'env-cc',
      categoryIds: [],
      isPaymentEnvelope: true,
      availableForPayment: -20,
      paymentUnderfunded: 0,
    })
    const { container } = renderRow(c)
    // Owner ask, 2026-09-15 — dropped on desktop and mobile alike; the mobile card's own
    // Available figure already carries the negative number.
    expect(container.querySelector('.pl-mcard-status')).toBeNull()
  })

  it("a payment envelope's underfunded status text renders in the mobile status slot too", () => {
    const c = mkCat({
      key: 'cc',
      label: 'Amex',
      envelopeId: 'env-cc',
      categoryIds: [],
      isPaymentEnvelope: true,
      availableForPayment: 90,
      paymentUnderfunded: 110,
    })
    const { container } = renderRow(c)
    const status = container.querySelector('.pl-mcard-status')!
    expect(status.textContent).toBe(`${fSmart(110)} more needed for this bill`)
  })

  it('a merged envelope shows its EnvCatsBadge chip inside the mobile card head', () => {
    const c = mkCat({
      key: 'fun',
      label: 'Fun',
      envelopeId: 'env-fun',
      categoryIds: ['fun', 'hobby'],
    })
    const { container } = renderRow(c)
    expect(container.querySelector('.pl-mcard-head')!.textContent).toContain('2 categories')
  })

  it('a zero-category envelope shows the "0 categories" hint inside the mobile card head', () => {
    const c = mkCat({
      key: 'env-empty',
      label: 'Emergency fund',
      envelopeId: 'env-empty',
      categoryIds: [],
    })
    const { container } = renderRow(c)
    expect(container.querySelector('.pl-mcard-head')!.textContent).toContain('0 categories')
  })
})

describe('GroupBlock — mobile card layout', () => {
  function mkGroup(
    overrides: Partial<LocalGroup> & Pick<LocalGroup, 'id' | 'name' | 'cats'>,
  ): LocalGroup {
    return {
      collapsed: false,
      synced: false,
      subtotal: { assigned: 0, activity: 0, available: 0 },
      ...overrides,
    }
  }
  function noopHandlers() {
    return {
      dnd: noopDnd(),
      over: null,
      month: '2026-07',
      allGroups: [] as LocalGroup[],
      catLabel: (k: string) => k,
      catColor: () => '#34b08a',
      rta: 0,
      onOpenCommitment: vi.fn(),
      onOpenCardPayment: vi.fn(),
      onAssign: vi.fn(),
      onAction: vi.fn(),
      onRename: vi.fn(),
      onRecolor: vi.fn(),
      onMoveTo: vi.fn(),
      onToggleHide: vi.fn(),
      onDeleteEnvelope: vi.fn(),
      onEditTarget: vi.fn(),
      onTransfer: vi.fn(),
      onMerge: vi.fn(),
      onSplit: vi.fn(),
      onAddCats: vi.fn(),
      onToggleCollapse: vi.fn(),
      onRenameGroup: vi.fn(),
      onDeleteGroup: vi.fn(),
      onAddCatStart: vi.fn(),
      onHideGroup: vi.fn(),
      onOpenCommitments: vi.fn(),
      onOpenCardPayments: vi.fn(),
    }
  }

  it('renders a .pl-mgroup header + card stack, never the desktop .pl-grow/.pl-trow row', () => {
    const cats = [
      mkCat({ key: 'dining', label: 'Dining', envelopeId: 'env-dining', categoryIds: ['dining'] }),
    ]
    const g = mkGroup({ id: 'g1', name: 'Everyday', cats })
    const { container } = render(<GroupBlock g={g} allGroups={[g]} h={noopHandlers()} />)
    expect(container.querySelector('.pl-mgroup')).toBeTruthy()
    expect(container.querySelector('.pl-mgroup-head')).toBeTruthy()
    expect(container.querySelector('.pl-mcard')).toBeTruthy()
    expect(container.querySelector('.pl-trow')).toBeNull()
    expect(screen.getByText('Everyday')).toBeTruthy()
    expect(screen.getByText('Dining')).toBeTruthy()
  })

  it('a synced (locked) group shows the Synced pill and no add/kebab, on mobile too', () => {
    const cats = [
      mkCat({ key: 'rent', label: 'Rent', envelopeId: 'env-rent', categoryIds: ['rent'] }),
    ]
    const g = mkGroup({ id: 'g2', name: 'Commitments', cats, synced: true })
    const { container } = render(<GroupBlock g={g} allGroups={[g]} h={noopHandlers()} />)
    expect(screen.getByText('Synced')).toBeTruthy()
    expect(container.querySelector('.pl-gadd')).toBeNull()
  })

  it('an unlocked group shows the add(+) button and its Kebab, never the Synced pill', () => {
    const cats = [
      mkCat({ key: 'dining', label: 'Dining', envelopeId: 'env-dining', categoryIds: ['dining'] }),
    ]
    const g = mkGroup({ id: 'g1', name: 'Everyday', cats })
    const { container } = render(<GroupBlock g={g} allGroups={[g]} h={noopHandlers()} />)
    expect(screen.queryByText('Synced')).toBeNull()
    expect(container.querySelector('.pl-gadd')).toBeTruthy()
    expect(screen.getByLabelText('Everyday group options')).toBeTruthy()
    expect(container.querySelector('.pl-gcount')!.textContent).toBe('1')
  })

  it('renders the correct group subtotal figures (assigned/activity/available) in its mobile header', () => {
    const cats = [
      mkCat({
        key: 'dining',
        label: 'Dining',
        envelopeId: 'env-dining',
        categoryIds: ['dining'],
        assigned: 100,
        activity: -30,
        available: 70,
      }),
    ]
    const g = mkGroup({
      id: 'g1',
      name: 'Everyday',
      cats,
      subtotal: { assigned: 100, activity: -30, available: 70 },
    })
    const { container } = render(<GroupBlock g={g} allGroups={[g]} h={noopHandlers()} />)
    const nums = container.querySelectorAll('.pl-mgroup-head .pl-mnum')
    expect(nums).toHaveLength(3)
    // Group subtotals render through fSmart (cents only when fractional), matching every
    // Activity/Available cell throughout the table — NOT fBase, which always shows 2 decimals
    // and is reserved for the header's hero/summary figures.
    expect(nums[0]!.textContent).toContain(fSmart(100))
    expect(nums[1]!.textContent).toContain(fSmart(-30))
    expect(nums[2]!.textContent).toContain(fSmart(70))
  })

  it('a payment group sums availableForPayment for its Available subtotal, not subtotal.available', () => {
    const cats = [
      mkCat({
        key: 'cc1',
        label: 'Visa',
        envelopeId: 'env-cc1',
        categoryIds: [],
        isPaymentEnvelope: true,
        availableForPayment: 40,
        available: 999,
      }),
      mkCat({
        key: 'cc2',
        label: 'Amex',
        envelopeId: 'env-cc2',
        categoryIds: [],
        isPaymentEnvelope: true,
        availableForPayment: -10,
        available: 999,
      }),
    ]
    const g = mkGroup({
      id: 'g3',
      name: 'Credit Card Payments',
      cats,
      synced: true,
      subtotal: { assigned: 0, activity: 0, available: 9999 },
    })
    const { container } = render(<GroupBlock g={g} allGroups={[g]} h={noopHandlers()} />)
    const nums = container.querySelectorAll('.pl-mgroup-head .pl-mnum')
    expect(nums[2]!.textContent).toContain(fSmart(30))
    expect(nums[2]!.textContent).not.toContain(fSmart(9999))
  })

  it('a collapsed group renders its header but no card body on mobile', () => {
    const cats = [
      mkCat({ key: 'dining', label: 'Dining', envelopeId: 'env-dining', categoryIds: ['dining'] }),
    ]
    const g = mkGroup({ id: 'g1', name: 'Everyday', cats, collapsed: true })
    const { container } = render(<GroupBlock g={g} allGroups={[g]} h={noopHandlers()} />)
    expect(container.querySelector('.pl-mgroup-head')).toBeTruthy()
    expect(container.querySelector('.pl-mgroup-body')).toBeNull()
    expect(container.querySelector('.pl-mcard')).toBeNull()
  })

  it('never renders a drag grip in the mobile group header or its cards (DnD is desktop-only)', () => {
    const cats = [
      mkCat({ key: 'dining', label: 'Dining', envelopeId: 'env-dining', categoryIds: ['dining'] }),
    ]
    const g = mkGroup({ id: 'g1', name: 'Everyday', cats })
    const { container } = render(<GroupBlock g={g} allGroups={[g]} h={noopHandlers()} />)
    expect(container.querySelector('.pl-grip')).toBeNull()
  })
})

describe('OutsideEnvelopes — mobile card layout', () => {
  it('renders its header + rows as cards, never the desktop .pl-trow grid', () => {
    const data: OutsideEnvelopesData = {
      unsortedActivity: -145.2,
      unsortedAvailable: -145.2,
      uncatIncome: 120,
      unbudgeted: [
        { key: 'pet_care', label: 'Pet care', activity: -86.4, available: -86.4, color: '#d4537e' },
      ],
    }
    const { container } = render(
      <OutsideEnvelopes
        data={data}
        month="2026-07"
        onReviewInAccounts={vi.fn()}
        onLabelIncome={vi.fn()}
        onAddToEnvelope={vi.fn()}
      />,
    )
    expect(container.querySelector('.pl-mgroup')).toBeTruthy()
    expect(container.querySelector('.pl-mcard')).toBeTruthy()
    expect(container.querySelector('.pl-trow')).toBeNull()
    expect(screen.getByText('Outside envelopes')).toBeTruthy()
    expect(screen.getByText('Uncategorized')).toBeTruthy()
    expect(screen.getByText('Pet care')).toBeTruthy()
  })

  it('a row action button fires its callback in the mobile card layout too', () => {
    const onAdd = vi.fn()
    const data: OutsideEnvelopesData = {
      unsortedActivity: 0,
      unsortedAvailable: 0,
      uncatIncome: 0,
      unbudgeted: [
        { key: 'pet_care', label: 'Pet care', activity: -86.4, available: -86.4, color: '#d4537e' },
      ],
    }
    render(
      <OutsideEnvelopes
        data={data}
        month="2026-07"
        onReviewInAccounts={vi.fn()}
        onLabelIncome={vi.fn()}
        onAddToEnvelope={onAdd}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add to envelope…' }))
    expect(onAdd).toHaveBeenCalledWith('pet_care', 'Pet care')
  })
})
