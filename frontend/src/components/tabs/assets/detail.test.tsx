// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { AssetCategory, AssetLoan, AssetMark, OwnedAsset } from '../../../types/assets'

// AssetDetail mounts MarkChart, which constructs a real Chart.js chart against a canvas jsdom
// can't rasterize — mock the constructor (same pattern as charts.test.tsx / chart.height.test).
vi.mock('chart.js', () => {
  class FakeChart {
    static register = vi.fn()
    data: { datasets: unknown[] } = { datasets: [] }
    options: { scales?: Record<string, { min?: number; max?: number } | undefined> } = {}
    update = vi.fn()
    destroy = vi.fn()
    constructor() {
      /* no-op */
    }
  }
  return {
    Chart: FakeChart,
    LineController: {},
    LineElement: {},
    PointElement: {},
    LinearScale: {},
    Tooltip: {},
    Filler: {},
  }
})

// Chart chrome colors are read via useTheme().resolved — pin it so the effect body runs
// deterministically without a real ThemeProvider.
vi.mock('../../../theme', () => ({
  useTheme: () => ({ preference: 'dark', resolved: 'dark', setPreference: vi.fn() }),
}))

import { fDate } from '../../../utils/format'
import { fmt } from './assetsData'
import { AssetDetail } from './detail'

afterEach(() => {
  cleanup()
})

function mark(overrides: Partial<AssetMark>): AssetMark {
  return {
    id: 'mk1',
    kind: 'valuation',
    date: '2026-01-01',
    value: 1000,
    note: 'purchase price',
    by: 'you',
    created_at: '2026-01-01T00:00:00Z',
    edited_at: null,
    ...overrides,
  }
}

function asset(overrides: Partial<OwnedAsset> = {}): OwnedAsset {
  return {
    id: 'oa_1',
    name: 'Test Watch',
    category: 'watch',
    description: 'box & papers',
    photo_ref: null,
    facts: [['Reference', '124060']],
    notes: '',
    cost: null,
    cost_date: null,
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: 1000,
    valued_on: '2026-01-01',
    share_value: 1000,
    marks: [mark({})],
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const CATS: AssetCategory[] = [
  {
    key: 'watch',
    label: 'Watch & jewellery',
    plural: 'Watches & jewellery',
    color: 'var(--cat-watch)',
    custom: false,
  },
]

const LOAN: AssetLoan = {
  id: 'l1',
  name: 'Lark Home Loan',
  balance: 500,
  rate_label: '3.1%',
  term_label: 'to 2044',
  held_by: null,
}

function renderDetail(overrides: Partial<OwnedAsset> = {}, loans: AssetLoan[] = []) {
  const a = asset(overrides)
  const handlers = {
    onBack: vi.fn(),
    onDispose: vi.fn(),
    onDelete: vi.fn(),
    onPhoto: vi.fn(),
    onNav: vi.fn(),
    onToast: vi.fn(),
  }
  const utils = render(<AssetDetail a={a} cats={CATS} loans={loans} {...handlers} />)
  return { a, ...handlers, ...utils }
}

describe('AssetDetail — scroll reset', () => {
  it('resets the shared .app-layout scroll on mount, and again when a DIFFERENT asset opens', () => {
    // jsdom doesn't implement Element.prototype.scrollTo at all — spyOn needs the property
    // to already exist, so assign a fake directly instead.
    const scrollTo = vi.fn()
    Element.prototype.scrollTo = scrollTo
    const handlers = {
      onBack: vi.fn(),
      onDispose: vi.fn(),
      onDelete: vi.fn(),
      onPhoto: vi.fn(),
      onNav: vi.fn(),
      onToast: vi.fn(),
    }
    const a1 = asset({ id: 'oa_1' })
    const { rerender } = render(
      <div className="app-layout">
        <AssetDetail a={a1} cats={CATS} loans={[]} {...handlers} />
      </div>,
    )
    expect(scrollTo).toHaveBeenCalledWith(0, 0)
    scrollTo.mockClear()

    // A re-render of the SAME asset (e.g. a data refresh) must not re-fire — the register's
    // own scroll position is meaningless here, but a fresh reset on every render would fight
    // the user's own scrolling on this page.
    rerender(
      <div className="app-layout">
        <AssetDetail a={a1} cats={CATS} loans={[]} {...handlers} />
      </div>,
    )
    expect(scrollTo).not.toHaveBeenCalled()

    // A DIFFERENT asset swapping in without unmounting (a cross-link, e.g. from a loan's own
    // "repayment plan →") must reset again.
    const a2 = asset({ id: 'oa_2', name: 'A different asset' })
    rerender(
      <div className="app-layout">
        <AssetDetail a={a2} cats={CATS} loans={[]} {...handlers} />
      </div>,
    )
    expect(scrollTo).toHaveBeenCalledWith(0, 0)

    delete (Element.prototype as { scrollTo?: unknown }).scrollTo
  })
})

describe('AssetDetail — header', () => {
  it('renders the breadcrumb, title, and the description alone as the subtitle', () => {
    const { container } = renderDetail({
      name: 'Rolex Submariner',
      category: 'watch',
      description: 'box & papers',
    })
    expect(container.querySelector('.as-crumb .cur')?.textContent).toBe('Rolex Submariner')
    expect(container.querySelector('.as-dtitle')?.textContent).toBe('Rolex Submariner')
    // The page's one heading.
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Rolex Submariner')
    // Subtitle is the description alone — the category no longer repeats here (the thumb's
    // own color/icon already carries it, and it's one tap away via the register).
    expect(container.querySelector('.as-card-sub')?.textContent).toBe('box & papers')
  })

  it('has no subtitle line at all when the asset has no description', () => {
    const { container } = renderDetail({ name: 'Plain thing', category: 'watch', description: '' })
    expect(container.querySelector('.as-card-sub')).toBeNull()
  })

  it('fires onBack from the breadcrumb', () => {
    const { onBack } = renderDetail()
    fireEvent.click(screen.getByText('Assets'))
    expect(onBack).toHaveBeenCalledTimes(1)
  })
})

describe('AssetDetail — value stat', () => {
  it('shows the valued-on month without review status', () => {
    const { container } = renderDetail({ valued_on: '2026-04-01' })
    expect(container.querySelector('.as-dstat-s')?.textContent).toContain('valued Apr 2026')
    expect(container.querySelector('.as-pip')).toBeNull()
  })

  it('offers Update value beside the ⋯ menu on desktop, opening its dialog', () => {
    const { container } = renderDetail()
    const button = screen.getByRole('button', { name: 'Update value' })
    const actions = container.querySelector('.as-dhead-actions')
    expect(actions?.contains(button)).toBe(true)
    expect(actions?.querySelector('.kebab-trigger')).toBeTruthy()
    expect(button.className).toContain('btn-primary')
    expect(container.querySelector('.as-dstats')?.contains(button)).toBe(false)
    fireEvent.click(button)
    expect(document.querySelector('.ui-modal__title')?.textContent).toBe('Update value')
    expect(document.querySelector('.ui-modal__subtitle')?.textContent).toBe('Test Watch')
  })

  it('on a phone, is one full-width button under all the figures — never inside Value', () => {
    const fn = ((query: string) => ({
      matches: /max-width/.test(query),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia
    const original = window.matchMedia
    Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
    try {
      const { container } = renderDetail()
      const button = screen.getByRole('button', { name: 'Update value' })
      expect(container.querySelector('.as-dstats')?.contains(button)).toBe(false)
      expect(container.querySelector('.as-dstats')?.nextElementSibling).toBe(button)
      expect(button.classList.contains('ui-btn--full')).toBe(true)
      expect(container.querySelector('.as-dhead-actions')?.contains(button)).toBe(false)
    } finally {
      Object.defineProperty(window, 'matchMedia', {
        value: original,
        configurable: true,
        writable: true,
      })
    }
  })

  it('reads "Your {pct}% · {share value}" only when the asset is shared', () => {
    const { container } = renderDetail({ value: 850000, share_pct: 50, share_value: 425000 })
    expect(container.querySelector('.as-dstat-share')?.textContent).toBe(
      `Your 50% · ${fmt(425000)}`,
    )
    cleanup()
    const whole = renderDetail({ share_pct: 100 })
    expect(whole.container.querySelector('.as-dstat-share')).toBeNull()
  })

  it('renders an imported asset with no value as "—", without crashing', () => {
    const { container } = renderDetail({
      value: null,
      valued_on: null,
      share_value: null,
      share_pct: 50,
      marks: [],
    })
    expect(container.querySelector('.as-dstat-v')?.textContent).toBe('—')
    expect(container.querySelector('.as-dstat-share')?.textContent).toBe('Your 50% · —')
    expect(container.querySelector('.as-chart')).toBeNull()
  })
})

describe('AssetDetail — value chart', () => {
  const legend = (c: HTMLElement) => c.querySelector('.as-chart-lg')?.textContent ?? ''

  it('labels the line "Value" and the purchase line by what it is', () => {
    const two = [
      mark({ id: 'm2', date: '2026-01-01', value: 1000 }),
      mark({ id: 'm1', date: '2025-01-01', value: 900 }),
    ]
    const bought = renderDetail({ cost: 800, cost_date: '2024-01-01', marks: two })
    expect(legend(bought.container)).toContain('Value')
    expect(legend(bought.container)).toContain('Bought for')
    expect(legend(bought.container)).not.toContain('Current price')
    cleanup()
    const policy = renderDetail({ category: 'policy', cost: 800, marks: two })
    expect(legend(policy.container)).toContain('Premiums paid')
  })

  it('says a single value is no trend yet, instead of a flat block', () => {
    const { container } = renderDetail({ marks: [mark({})] })
    expect(legend(container)).toContain('One value so far — update it to see a trend')
  })
})

describe('AssetDetail — net equity', () => {
  it('is shown only when a linked loan has a positive balance', () => {
    const { container } = renderDetail({ loan_account_id: 'l1', value: 1000 }, [LOAN])
    expect(container.querySelector('.as-dstat-k')?.textContent).toBe('Value')
    const keys = Array.from(container.querySelectorAll('.as-dstat-k')).map((el) => el.textContent)
    expect(keys).toContain('Net equity')
  })

  it('says the equity is of the whole asset, so a shared asset cannot misread it', () => {
    const { container } = renderDetail(
      { loan_account_id: 'l1', value: 1000, share_pct: 50, share_value: 500 },
      [LOAN],
    )
    const subs = Array.from(container.querySelectorAll('.as-dstat-s')).map((el) => el.textContent)
    expect(subs).toContain('Whole asset · 50% of value')
  })

  it('is hidden when there is no loan', () => {
    const { container } = renderDetail({ loan_account_id: null })
    const keys = Array.from(container.querySelectorAll('.as-dstat-k')).map((el) => el.textContent)
    expect(keys).not.toContain('Net equity')
  })

  it('is hidden when the linked loan is fully paid off', () => {
    const paidOff: AssetLoan = { ...LOAN, balance: 0 }
    const { container } = renderDetail({ loan_account_id: 'l1' }, [paidOff])
    const keys = Array.from(container.querySelectorAll('.as-dstat-k')).map((el) => el.textContent)
    expect(keys).not.toContain('Net equity')
  })
})

describe('AssetDetail — unrealized', () => {
  it('reads "No purchase price yet" when cost is null', () => {
    const { container } = renderDetail({ cost: null })
    const subs = container.querySelectorAll('.as-dstat-s')
    expect(subs[subs.length - 1]?.textContent).toBe('No purchase price yet')
  })

  it('shows the gain percentage and years held (since the purchase date) when cost is recorded', () => {
    const { container } = renderDetail({
      cost: 800,
      cost_date: '2024-01-01',
      value: 1000,
      valued_on: '2026-01-01',
      marks: [
        mark({ id: 'm2', date: '2026-01-01', value: 1000 }),
        mark({ id: 'purchase', kind: 'purchase', date: '2024-01-01', value: 800 }),
      ],
    })
    const subs = container.querySelectorAll('.as-dstat-s')
    const sub = subs[subs.length - 1]?.textContent ?? ''
    expect(sub).toContain('+25.0%')
    expect(sub).toContain('2.0 yr')
  })
})

describe('AssetDetail — sub-tabs', () => {
  it('shows the Value history newest-first with signed change, by default', () => {
    expect(renderDetail().getByRole('tab', { name: 'Value history' })).toBeTruthy()
    cleanup()
    const { container } = renderDetail({
      marks: [
        mark({ id: 'm2', date: '2026-06-01', value: 1300, note: 'appraisal' }),
        mark({ id: 'm1', date: '2025-06-01', value: 1000, note: 'purchase price' }),
      ],
    })
    const rows = container.querySelectorAll('.as-tbl tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0]?.textContent).toContain(fDate('2026-06-01', 'short', 'always'))
    expect(rows[0]?.textContent).toContain('appraisal')
    expect(rows[0]?.textContent).toContain('+') // gained vs. the older mark
    expect(rows[1]?.textContent).toContain(fDate('2025-06-01', 'short', 'always'))
    expect(rows[1]?.textContent).toContain('—') // oldest row has no prior mark to diff
  })

  it('switches to Documents and shows the empty state with no docs', () => {
    renderDetail({ docs: [] })
    fireEvent.click(screen.getByText('Documents'))
    expect(screen.getByText(/No documents yet/)).toBeTruthy()
  })

  it('lists attachments by name with the Added date, on the Documents tab', () => {
    renderDetail({
      docs: [
        { ref_id: 'ref1', name: 'invoice.pdf', added_on: '2026-02-01', lucius_readable: true },
      ],
    })
    fireEvent.click(screen.getByText('Documents'))
    expect(screen.getByText('invoice.pdf')).toBeTruthy()
    expect(screen.getByText('Feb 2026')).toBeTruthy()
    expect(screen.getByRole('switch')).toBeTruthy()
  })
})

describe('AssetDetail — rail and its editors', () => {
  const dialogTitle = () => document.querySelector('.ui-modal__title')?.textContent

  it('shows the record — Ownership, Purchase, Details and Notes — and no "Added" caption', () => {
    const { container } = renderDetail(
      {
        loan_account_id: 'l1',
        value: 1000,
        cost: 800,
        cost_date: '2025-01-01',
        facts: [['Brand & reference', 'Rolex 124060']],
        notes: 'Box and papers in the safe.',
      },
      [LOAN],
    )
    const titles = Array.from(container.querySelectorAll('.as-rec-title')).map(
      (el) => el.textContent,
    )
    expect(titles).toEqual(['Ownership', 'Purchase', 'Details', 'Notes'])
    expect(container.textContent).toContain('Lark Home Loan')
    expect(container.querySelector('.as-added-caption')).toBeNull()
    expect(container.textContent).not.toContain('Added Jan 2026')
  })

  it('shows the sections in the page itself — after the value and chart, before Value history', () => {
    const { container } = renderDetail({ loan_account_id: 'l1', value: 1000 }, [LOAN])
    expect(container.querySelector('.as-rail')).toBeNull()
    expect(container.querySelector('.as-body')?.classList.contains('as-dbody')).toBe(true)
    const follows = (a: Element | null, b: Element | null) =>
      Boolean(a && b && a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    const sections = container.querySelector('.as-record')
    expect(follows(container.querySelector('.as-dstats'), sections)).toBe(true)
    const chart = container.querySelector('.as-chart')
    if (chart) expect(follows(chart, sections)).toBe(true)
    expect(follows(sections, container.querySelector('.as-subtabs'))).toBe(true)
  })

  it("opens each section's own editor, one at a time", () => {
    renderDetail({ cost: 800, cost_date: '2025-01-01' })
    fireEvent.click(screen.getByRole('button', { name: 'Edit ownership' }))
    expect(dialogTitle()).toBe('Ownership')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit purchase' }))
    expect(dialogTitle()).toBe('Purchase')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    // Nothing in Details or Notes yet: their "+" chips open the same editors.
    fireEvent.click(screen.getByRole('button', { name: 'Add brand & reference' }))
    expect(dialogTitle()).toBe('Details')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add a note' }))
    expect(dialogTitle()).toBe('Notes')
  })

  it("the purchase row's Edit purchase opens the Purchase editor", () => {
    renderDetail({
      cost: 800,
      cost_date: '2025-01-01',
      marks: [
        mark({ id: 'm1', date: '2026-01-01', value: 1000 }),
        mark({ id: 'purchase', kind: 'purchase', date: '2025-01-01', value: 800 }),
      ],
    })
    const row = document.querySelectorAll('.as-tbl tbody tr')[1] as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: /^Edit the purchase from/ }))
    expect(dialogTitle()).toBe('Purchase')
  })
})

describe('AssetDetail — ⋯ menu', () => {
  const items = () => screen.getAllByRole('menuitem').map((el) => el.textContent)

  it('holds Rename or change category, the photo, Sell or dispose and Delete asset', () => {
    // Owner 2026-09-28: tapping the photo opens it — so adding or changing it lives here too.
    renderDetail()
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    expect(items()).toEqual([
      'Rename or change category',
      'Add photo',
      'Sell or dispose',
      'Delete asset',
    ])
    cleanup()
    renderDetail({ photo_ref: 'att_photo00001' })
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    expect(items()).toContain('Change photo')
  })

  it('renames on the page; sells and deletes through the shell, with the asset', () => {
    const { onDispose, onDelete, a } = renderDetail()
    const pick = (label: string) => {
      fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
      fireEvent.click(screen.getByRole('menuitem', { name: label }))
    }
    pick('Rename or change category')
    expect(document.querySelector('.ui-modal__title')?.textContent).toBe(
      'Rename or change category',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    pick('Sell or dispose')
    expect(onDispose).toHaveBeenCalledWith(a)
    pick('Delete asset')
    expect(onDelete).toHaveBeenCalledWith(a)
  })
})

describe('AssetDetail — opens at the top on desktop too', () => {
  it('scrolls the window to the top, not only the phone scroller', () => {
    // Owner 2026-09-28: "when open from assets list to asset detail, the asset detail doesn't
    // start from top" — a desktop scrolls the window, which the page never reset.
    Element.prototype.scrollTo = vi.fn()
    const winScroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    const handlers = {
      onBack: vi.fn(),
      onDispose: vi.fn(),
      onDelete: vi.fn(),
      onPhoto: vi.fn(),
      onNav: vi.fn(),
      onToast: vi.fn(),
    }
    const { rerender } = render(
      <AssetDetail a={asset({ id: 'oa_1' })} cats={CATS} loans={[]} {...handlers} />,
    )
    expect(winScroll).toHaveBeenCalledWith({ top: 0, left: 0, behavior: 'instant' })
    winScroll.mockClear()
    rerender(<AssetDetail a={asset({ id: 'oa_2' })} cats={CATS} loans={[]} {...handlers} />)
    expect(winScroll).toHaveBeenCalledTimes(1)
    winScroll.mockRestore()
  })
})
