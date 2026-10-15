// @vitest-environment jsdom
//
// Value history — the desktop table, the phone's two-line list, and each row's correct/remove
// actions, against a mocked authFetch (the real api.ts parses every answer). Synthetic data only.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { AssetMark, OwnedAsset } from '../../../types/assets'
import { fDate, setBaseCurrency } from '../../../utils/format'
import { fmt } from './assetsData'
import { ValueHistory } from './ValueHistory'

/** A recorded value's date as the history writes it — the year always shown. */
const day = (iso: string) => fDate(iso, 'short', 'always')

const { authFetch } = vi.hoisted(() => ({ authFetch: vi.fn() }))
vi.mock('../../../api/client', () => ({ authFetch }))

setBaseCurrency('SGD')

function mark(overrides: Partial<AssetMark>): AssetMark {
  return {
    id: 'mk_1',
    kind: 'valuation',
    date: '2026-09-26',
    value: 850000,
    note: '',
    by: 'you',
    created_at: '2026-09-26T00:00:00Z',
    edited_at: null,
    ...overrides,
  }
}

const NEWEST = mark({ id: 'mk_new', date: '2026-09-26', value: 850000 })
const OLDER = mark({ id: 'mk_old', date: '2024-03-02', value: 700000, note: 'Agent estimate' })
const PURCHASE = mark({ id: 'purchase', kind: 'purchase', date: '2019-06-15', value: 620000 })

function asset(marks: AssetMark[]): OwnedAsset {
  return {
    id: 'oa_test000001',
    name: 'Test Condo',
    category: 'property',
    description: '',
    photo_ref: null,
    facts: [],
    notes: '',
    cost: 620000,
    cost_date: '2019-06-15',
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: marks[0]?.value ?? null,
    valued_on: marks[0]?.date ?? null,
    share_value: marks[0]?.value ?? null,
    marks,
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2019-06-15T00:00:00Z',
    updated_at: '2026-09-26T00:00:00Z',
  }
}

function ok(data: unknown): Response {
  return { ok: true, status: 200, json: async () => data } as Response
}

function refused(status: number, body: unknown): Response {
  return { ok: false, status, json: async () => body } as Response
}

/** A viewport of `px` wide for useIsMobile's max-width query. */
function viewport(px: number) {
  const fn = ((query: string) => {
    const max = query.match(/max-width:\s*([\d.]+)px/)
    return {
      matches: max ? px <= parseFloat(max[1]!) : false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }
  }) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
}

function renderHistory(marks: AssetMark[]) {
  const props = { a: asset(marks), onEditPurchase: vi.fn(), onToast: vi.fn() }
  const utils = render(<ValueHistory {...props} />)
  return { ...props, ...utils }
}

const rows = () => Array.from(document.querySelectorAll('.as-tbl tbody tr')) as HTMLElement[]

beforeEach(() => {
  authFetch.mockReset()
  viewport(1440)
})
afterEach(cleanup)

describe('ValueHistory — desktop table', () => {
  it('lists every point newest first, with full dates and the change from the one before', () => {
    renderHistory([NEWEST, OLDER, PURCHASE])
    const [first, second, third] = rows()
    expect(first?.textContent).toContain(day('2026-09-26'))
    expect(first?.textContent).toContain(`+${fmt(150000)}`)
    expect(first?.textContent).toContain('—') // no note
    expect(second?.textContent).toContain(day('2024-03-02'))
    expect(second?.textContent).toContain('Agent estimate')
    expect(third?.textContent).toContain(day('2019-06-15'))
    expect(third?.textContent?.replace(fmt(620000), '')).toContain('—') // the oldest: no change
  })

  it('makes each row the control — no per-row icon buttons', () => {
    renderHistory([NEWEST, OLDER, PURCHASE])
    expect(
      screen.queryByRole('button', { name: /Correct this value|Remove this value/ }),
    ).toBeNull()
    expect(document.querySelector('.as-fact-x')).toBeNull()
    const names = rows().map((r) => within(r).getByRole('button').getAttribute('aria-label'))
    // The name is all a screen reader hears of the row, so it carries the amount.
    expect(names).toEqual([
      `Edit the value from ${day('2026-09-26')}, ${fmt(850000)}`,
      `Edit the value from ${day('2024-03-02')}, ${fmt(700000)}`,
      `Edit the purchase from ${day('2019-06-15')}, ${fmt(620000)}`,
    ])
    expect(rows().every((r) => r.classList.contains('data'))).toBe(true)
  })

  it('opens the purchase row — "Bought" — in the Purchase editor, never a value dialog', () => {
    const { onEditPurchase } = renderHistory([NEWEST, PURCHASE])
    const purchase = rows()[1]!
    expect(purchase.textContent).toContain('Bought')
    fireEvent.click(purchase)
    expect(onEditPurchase).toHaveBeenCalledTimes(1)
    expect(document.querySelector('.ui-modal__title')).toBeNull()
  })

  it('opens Correct this value prefilled from the row clicked', () => {
    renderHistory([NEWEST, OLDER, PURCHASE])
    fireEvent.click(rows()[1]!)
    expect(document.querySelector('.ui-modal__title')?.textContent).toBe('Correct this value')
    const value = screen.getByRole('textbox', { name: 'Value' }).getAttribute('value')
    expect(value?.replace(/,/g, '')).toBe('700000')
    expect((screen.getByLabelText('As of') as HTMLInputElement).value).toBe('2024-03-02')
  })

  it('keeps the only value: Correct works, Remove value is refused with its reason', () => {
    renderHistory([NEWEST, PURCHASE])
    fireEvent.click(
      within(rows()[0]!).getByRole('button', {
        name: `Edit the value from ${day('2026-09-26')}, ${fmt(850000)}`,
      }),
    )
    const remove = screen.getByRole('button', { name: 'Remove value' }) as HTMLButtonElement
    expect(remove.disabled).toBe(true)
    expect(screen.getByText('An asset needs at least one value.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Save correction' })).toBeTruthy()
  })

  it('removes a value from inside its dialog, after a plain-words confirm, and toasts', async () => {
    authFetch.mockResolvedValueOnce(ok(asset([NEWEST])))
    const { onToast } = renderHistory([NEWEST, OLDER])
    fireEvent.click(rows()[1]!)
    const remove = screen.getByRole('button', { name: 'Remove value' })
    expect(remove.className).toContain('as-footer-danger')
    fireEvent.click(remove)
    expect(screen.getByText('Remove this value?')).toBeTruthy()
    expect(
      screen.getByText(
        `This removes the ${day('2024-03-02')} entry — ${fmt(700000)}. This can't be undone.`,
      ),
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(onToast).toHaveBeenCalledWith('Value removed.'))
    expect(authFetch).toHaveBeenCalledWith('/api/assets/oa_test000001/marks/mk_old', {
      method: 'DELETE',
    })
    expect(screen.queryByText('Remove this value?')).toBeNull()
    expect(document.querySelector('.ui-modal__title')).toBeNull()
  })

  it('a refused removal stays in the confirm with the reason', async () => {
    const detail =
      'An asset needs at least one value. Record a new value first, or delete the asset.'
    authFetch.mockResolvedValueOnce(
      refused(409, { detail, code: 'conflict', reason: 'last_value' }),
    )
    const { onToast } = renderHistory([NEWEST, OLDER])
    fireEvent.click(rows()[1]!)
    fireEvent.click(screen.getByRole('button', { name: 'Remove value' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect((await screen.findByRole('alert')).textContent).toBe(detail)
    expect(screen.getByText('Remove this value?')).toBeTruthy()
    expect(onToast).not.toHaveBeenCalled()
  })

  it('drops the By column — an automation says so in the note instead', () => {
    renderHistory([mark({ id: 'mk_auto', by: 'automation', note: 'Price feed' }), OLDER])
    const heads = Array.from(document.querySelectorAll('.as-tbl thead th')).map(
      (th) => th.textContent,
    )
    expect(heads).not.toContain('By')
    expect(rows()[0]?.textContent).toContain('Price feed · automation')
    expect(rows()[1]?.textContent).not.toContain('you')
  })

  it('shows no change as a plain S$0 — neither green nor signed', () => {
    renderHistory([mark({ id: 'mk_same', value: 700000 }), OLDER])
    const cell = rows()[0]!.querySelectorAll('td')[3]!
    expect(cell.textContent).toBe(fmt(0))
    expect(cell.querySelector('.ui-text--good')).toBeNull()
    expect(cell.querySelector('.ui-text--muted')).not.toBeNull()
  })

  it('renders a legacy value with no note, never edited, without crashing', () => {
    renderHistory([mark({ id: 'mk_legacy', note: '', edited_at: null })])
    expect(rows()).toHaveLength(1)
  })

  it('says there are no values yet for an imported asset with none', () => {
    renderHistory([])
    expect(rows()).toHaveLength(0)
    expect(screen.getByText('No values yet')).toBeTruthy()
  })
})

describe('ValueHistory — phone (390px)', () => {
  beforeEach(() => viewport(390))

  it('is a two-line list of row buttons, not a squeezed table', () => {
    const { onEditPurchase } = renderHistory([NEWEST, OLDER, PURCHASE])
    expect(document.querySelector('.as-tbl')).toBeNull()
    const items = Array.from(document.querySelectorAll('.as-history-list-row')) as HTMLElement[]
    expect(items).toHaveLength(3)
    expect(items.every((i) => i.tagName === 'BUTTON')).toBe(true)
    expect(items[0]?.textContent).toContain(day('2026-09-26'))
    expect(items[0]?.textContent).toContain(fmt(850000))
    expect(items[0]?.getAttribute('aria-label')).toBe(
      `Edit the value from ${day('2026-09-26')}, ${fmt(850000)}`,
    )
    expect(items[1]?.textContent).toContain('Agent estimate')
    expect(items[2]?.getAttribute('aria-label')).toBe(
      `Edit the purchase from ${day('2019-06-15')}, ${fmt(620000)}`,
    )
    expect(
      screen.queryByRole('button', { name: /Correct this value|Remove this value/ }),
    ).toBeNull()
    fireEvent.click(items[0]!)
    expect(document.querySelector('.ui-modal__title')?.textContent).toBe('Correct this value')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.click(items[2]!)
    expect(onEditPurchase).toHaveBeenCalledTimes(1)
  })

  it('names who recorded a value only when it was an automation', () => {
    renderHistory([mark({ id: 'mk_auto', by: 'automation' }), OLDER])
    const items = Array.from(document.querySelectorAll('.as-history-list-row'))
    expect(items[0]?.querySelector('.as-history-note')?.textContent).toBe('automation')
    expect(items[1]?.textContent).not.toContain('you')
  })

  it('puts no "—" in for a missing note or change — with neither, the row is one line', () => {
    renderHistory([NEWEST, mark({ id: 'mk_first', date: '2020-01-10', value: 700000 })])
    const [newest, first] = Array.from(
      document.querySelectorAll('.as-history-list-row'),
    ) as HTMLElement[]
    // A change and no note: the change keeps its place under the value.
    expect(newest!.textContent).not.toContain('—')
    expect(newest!.querySelector('.as-history-note')).toBeNull()
    expect(newest!.querySelector('.as-history-change')?.textContent).toBe(`+${fmt(150000)}`)
    expect(newest!.classList.contains('one-line')).toBe(false)
    // The oldest value, with no note: date, value and › on one line.
    expect(first!.textContent).not.toContain('—')
    expect(first!.querySelector('.as-history-note, .as-history-change')).toBeNull()
    expect(first!.classList.contains('one-line')).toBe(true)
  })
})
