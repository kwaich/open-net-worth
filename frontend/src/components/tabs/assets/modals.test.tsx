// @vitest-environment jsdom
//
// Sell or dispose, and the delete confirmation, against a mocked authFetch — the real api.ts
// parses every answer, so a refusal is exactly what the server would send (RFC §3.1, §3.8).
// Both wait for the server and keep what was typed when refused. Synthetic fixtures only;
// dialogs render through a Radix portal, so queries go through `screen` / `document`.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { CashAccountOption, OwnedAsset } from '../../../types/assets'
import { localIso, setBaseCurrency } from '../../../utils/format'
import { fmt } from './assetsData'
import { DeleteAssetConfirm, DisposeModal } from './modals'

const { authFetch } = vi.hoisted(() => ({ authFetch: vi.fn() }))
vi.mock('../../../api/client', () => ({ authFetch }))

setBaseCurrency('SGD')

const CASH_ACCOUNTS: CashAccountOption[] = [
  { id: 'c1', name: 'Test Bank', balance_base: 50000 },
  { id: 'c2', name: 'Second Bank', balance_base: 20000 },
]

function makeAsset(overrides: Partial<OwnedAsset> = {}): OwnedAsset {
  return {
    id: 'oa_test000001',
    name: 'Test Watch',
    category: 'watch',
    description: '',
    photo_ref: null,
    facts: [],
    notes: '',
    cost: 9000,
    cost_date: '2019-08-01',
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: 14800,
    valued_on: '2025-07-01',
    share_value: 14800,
    marks: [],
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2019-08-01T00:00:00Z',
    updated_at: '2025-07-01T00:00:00Z',
    ...overrides,
  }
}

function ok(data: unknown): Response {
  return { ok: true, status: 200, json: async () => data } as Response
}

function refused(status: number, body: unknown): Response {
  return { ok: false, status, json: async () => body } as Response
}

const title = () => document.querySelector('.ui-modal__title')?.textContent
const subtitle = () => document.querySelector('.ui-modal__subtitle')?.textContent
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole('button', { name }))
const soldOn = () => screen.getByLabelText('Sold on') as HTMLInputElement

function typeMoney(label: string | RegExp, text: string) {
  const input = screen.getByRole('textbox', { name: label })
  fireEvent.focus(input)
  fireEvent.change(input, { target: { value: text } })
  fireEvent.blur(input)
}

// A block body: an arrow returning the spy would hand Vitest a teardown that calls it.
beforeEach(() => {
  authFetch.mockReset()
})
afterEach(cleanup)

describe('DisposeModal', () => {
  function renderDispose(asset = makeAsset()) {
    const props = { asset, cashAccounts: CASH_ACCOUNTS, onClose: vi.fn(), onDisposed: vi.fn() }
    render(<DisposeModal {...props} />)
    return props
  }

  it('asks what it sold for, when, and where the money went', () => {
    renderDispose()
    expect(title()).toBe('Sell or dispose')
    expect(subtitle()).toBe('Test Watch')
    expect(screen.getByRole('textbox', { name: 'Sold for' }).getAttribute('value')).toBe('14,800')
    expect(soldOn().value).toBe(localIso())
    expect(soldOn().max).toBe(localIso())
    expect(soldOn().min).toBe('2019-08-01')
    const into = screen.getByRole('combobox', { name: 'Proceeds into' }) as HTMLSelectElement
    expect(into.value).toBe('c1')
    expect(into.selectedOptions[0]?.textContent).toBe(`Test Bank · ${fmt(50000)}`)
    expect(document.body.textContent).toContain(
      `Realized gain +${fmt(5800)} against a purchase price of ${fmt(9000)}.`,
    )
    expect(document.body.textContent).toContain(
      'The asset is archived, not deleted. Its full value history and documents stay readable, and it drops out of net worth from the disposal date.',
    )
    expect(document.body.textContent).not.toMatch(/\bmark\b/i)
    // A plain line, not a boxed hint: only the realised gain keeps its box.
    const note = screen.getByText(/The asset is archived, not deleted/)
    expect(note.tagName).toBe('P')
    expect(note.closest('.as-hint')).toBeNull()
  })

  it('records the sale with its date and waits for the server before handing back', async () => {
    let answer: (r: Response) => void = () => {}
    authFetch.mockReturnValueOnce(new Promise<Response>((resolve) => (answer = resolve)))
    const props = renderDispose()
    typeMoney('Sold for', '15000')
    fireEvent.change(soldOn(), { target: { value: '2026-09-01' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Proceeds into' }), {
      target: { value: 'c2' },
    })
    click('Record disposal')
    const busy = screen.getByRole('button', { name: 'Saving…' }) as HTMLButtonElement
    expect(busy.disabled).toBe(true)
    expect(props.onDisposed).not.toHaveBeenCalled()
    answer(ok(makeAsset({ archived: true })))
    await waitFor(() => expect(props.onDisposed).toHaveBeenCalledTimes(1))
    const [path, init] = authFetch.mock.calls[0]!
    expect(path).toBe('/api/assets/oa_test000001/dispose')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      proceeds: 15000,
      account_id: 'c2',
      date: '2026-09-01',
    })
  })

  it('keeps a price committed in the same tick as the sale date (autofill, fill())', async () => {
    authFetch.mockResolvedValueOnce(ok(makeAsset({ archived: true })))
    const props = renderDispose()
    const price = screen.getByRole('textbox', { name: 'Sold for' })
    fireEvent.focus(price)
    fireEvent.change(price, { target: { value: '15000' } })
    act(() => {
      fireEvent.blur(price)
      fireEvent.change(soldOn(), { target: { value: '2026-09-01' } })
    })
    click('Record disposal')
    await waitFor(() => expect(props.onDisposed).toHaveBeenCalled())
    expect(JSON.parse(String((authFetch.mock.calls[0]![1] as RequestInit).body))).toMatchObject({
      proceeds: 15000,
      date: '2026-09-01',
    })
  })

  it('409 before_purchase: stays open with the reason beside Sold on, nothing lost', async () => {
    const detail = "The sale date can't be before the purchase (1 Aug 2019)."
    authFetch.mockResolvedValueOnce(
      refused(409, { detail, code: 'conflict', reason: 'before_purchase', field: 'date' }),
    )
    const props = renderDispose()
    fireEvent.change(soldOn(), { target: { value: '2019-01-01' } })
    click('Record disposal')
    expect((await screen.findByRole('alert')).textContent).toBe(detail)
    expect(soldOn().getAttribute('aria-describedby')).toBe(
      screen.getByRole('alert').getAttribute('id'),
    )
    expect(soldOn().value).toBe('2019-01-01')
    expect(title()).toBe('Sell or dispose')
    expect(props.onDisposed).not.toHaveBeenCalled()
  })

  it('a refusal naming no field is said in the dialog', async () => {
    authFetch.mockResolvedValueOnce(
      refused(409, { detail: 'This asset was already sold differently.', code: 'conflict' }),
    )
    renderDispose()
    click('Record disposal')
    expect((await screen.findByRole('alert')).textContent).toBe(
      'This asset was already sold differently.',
    )
    expect(title()).toBe('Sell or dispose')
  })

  it('refuses a sale dated in the future before sending anything', () => {
    renderDispose()
    fireEvent.change(soldOn(), { target: { value: '2999-01-01' } })
    click('Record disposal')
    expect(screen.getByRole('alert').textContent).toBe("Pick a date that isn't in the future.")
    expect(authFetch).not.toHaveBeenCalled()
  })

  it('moving a future sale date back clears its error', () => {
    renderDispose()
    fireEvent.change(soldOn(), { target: { value: '2999-01-01' } })
    click('Record disposal')
    expect(soldOn().classList.contains('ui-input--invalid')).toBe(true)
    fireEvent.change(soldOn(), { target: { value: '2026-09-01' } })
    expect(screen.queryByText("Pick a date that isn't in the future.")).toBeNull()
    expect(soldOn().classList.contains('ui-input--invalid')).toBe(false)
  })

  it('waits for a cash account to put the money in — and says so beside Proceeds into', () => {
    render(
      <DisposeModal asset={makeAsset()} cashAccounts={[]} onClose={vi.fn()} onDisposed={vi.fn()} />,
    )
    expect(
      (screen.getByRole('button', { name: 'Record disposal' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    const into = screen.getByRole('combobox', { name: 'Proceeds into' })
    const said = screen.getByRole('alert')
    expect(said.textContent).toBe('Pick a cash account in SGD.')
    expect(into.getAttribute('aria-describedby')).toBe(said.getAttribute('id'))
  })
})

describe("DisposeModal — a co-owner sells their share, never the whole asset's value", () => {
  // A 50% condo worth 900,000, bought for 620,000: net worth counts 450,000 of it.
  function renderCondo(overrides: Partial<OwnedAsset> = {}) {
    const asset = makeAsset({
      name: 'Test Condo',
      category: 'property',
      value: 900000,
      cost: 620000,
      share_pct: 50,
      share_value: 450000,
      ...overrides,
    })
    const props = { asset, cashAccounts: CASH_ACCOUNTS, onClose: vi.fn(), onDisposed: vi.fn() }
    render(<DisposeModal {...props} />)
    return props
  }

  it('prefills your share of the value, says so, and posts exactly that', async () => {
    authFetch.mockResolvedValueOnce(ok(makeAsset({ archived: true })))
    const props = renderCondo()
    expect(screen.queryByRole('textbox', { name: 'Sold for' })).toBeNull()
    const field = screen.getByRole('textbox', { name: 'Your share of the sale' })
    expect(field.getAttribute('value')).toBe('450,000')
    expect(document.body.textContent).toContain('Your 50% — what lands in the account below.')
    click('Record disposal')
    await waitFor(() => expect(props.onDisposed).toHaveBeenCalled())
    expect(JSON.parse(String((authFetch.mock.calls[0]![1] as RequestInit).body)).proceeds).toBe(
      450000,
    )
  })

  it('measures the gain against your share of the purchase price', () => {
    renderCondo()
    expect(document.body.textContent).toContain(
      `Realized gain +${fmt(140000)} against your 50% of the purchase price, ${fmt(310000)}.`,
    )
    typeMoney('Your share of the sale', '300000')
    expect(document.body.textContent).toContain(
      `Realized gain −${fmt(10000)} against your 50% of the purchase price, ${fmt(310000)}.`,
    )
  })

  it('says a fractional share the way the page does', () => {
    renderCondo({ share_pct: 33.5, share_value: 301500 })
    expect(
      screen.getByRole('textbox', { name: 'Your share of the sale' }).getAttribute('value'),
    ).toBe('301,500')
    expect(document.body.textContent).toContain('Your 33.5% — what lands in the account below.')
    expect(document.body.textContent).toContain(
      `against your 33.5% of the purchase price, ${fmt(207700)}.`,
    )
  })

  it('a whole asset still prefills its whole value, with the wording as before', () => {
    renderCondo({ share_pct: 100, share_value: 900000 })
    expect(screen.getByRole('textbox', { name: 'Sold for' }).getAttribute('value')).toBe('900,000')
    expect(document.body.textContent).not.toContain('what lands in the account below')
    expect(document.body.textContent).toContain(
      `Realized gain +${fmt(280000)} against a purchase price of ${fmt(620000)}.`,
    )
  })
})

describe('DisposeModal — an amount it cannot read is never sent', () => {
  function renderDispose() {
    const props = {
      asset: makeAsset(),
      cashAccounts: CASH_ACCOUNTS,
      onClose: vi.fn(),
      onDisposed: vi.fn(),
    }
    render(<DisposeModal {...props} />)
    return props
  }

  it('posts nothing for "Sold for" text it cannot read — the prefill is never used', async () => {
    const props = renderDispose()
    typeMoney('Sold for', 'about 820 thousand')
    click('Record disposal')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(authFetch).not.toHaveBeenCalled()
    expect(props.onDisposed).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toBe('Enter an amount of 0 or more.')
    expect(screen.getByRole('textbox', { name: 'Sold for' }).getAttribute('value')).toBe(
      'about 820 thousand',
    )
  })

  it('reads "S$820,000" as 820000', async () => {
    authFetch.mockResolvedValueOnce(ok(makeAsset({ archived: true })))
    const props = renderDispose()
    typeMoney('Sold for', 'S$820,000')
    click('Record disposal')
    await waitFor(() => expect(props.onDisposed).toHaveBeenCalled())
    expect(JSON.parse(String((authFetch.mock.calls[0]![1] as RequestInit).body)).proceeds).toBe(
      820000,
    )
  })
})

describe('DeleteAssetConfirm', () => {
  function renderDelete() {
    const props = { asset: makeAsset(), onClose: vi.fn(), onDeleted: vi.fn() }
    render(<DeleteAssetConfirm {...props} />)
    return props
  }

  it('says what goes, and to sell it instead to keep its history', () => {
    renderDelete()
    expect(title()).toBe('Delete this asset?')
    expect(document.querySelector('.ui-confirm-body')?.textContent).toBe(
      "Delete Test Watch and its history? This can't be undone. If you sold it, use Sell or dispose instead — that keeps its history.",
    )
  })

  it('deletes, then hands back', async () => {
    authFetch.mockResolvedValueOnce(ok({}))
    const props = renderDelete()
    click('Delete')
    await waitFor(() => expect(props.onDeleted).toHaveBeenCalledTimes(1))
    expect(authFetch).toHaveBeenCalledWith('/api/assets/oa_test000001', { method: 'DELETE' })
  })

  it('a refused delete stays open with the reason', async () => {
    authFetch.mockResolvedValueOnce(
      refused(404, { detail: "That asset ('oa_test000001') no longer exists.", code: 'not_found' }),
    )
    const props = renderDelete()
    click('Delete')
    expect((await screen.findByRole('alert')).textContent).toBe(
      "That asset ('oa_test000001') no longer exists.",
    )
    expect(title()).toBe('Delete this asset?')
    expect(props.onDeleted).not.toHaveBeenCalled()
  })
})
