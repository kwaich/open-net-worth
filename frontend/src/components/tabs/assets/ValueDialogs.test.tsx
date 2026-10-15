// @vitest-environment jsdom
//
// Update value / Correct this value against a mocked authFetch — the real api.ts parses every
// response, so a refusal here is exactly what the server would send (RFC §3.1, §3.5–3.6).
// Synthetic fixtures only. The dialogs render through a Radix portal: queries go via `screen`.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { AssetMark, OwnedAsset } from '../../../types/assets'
import { localIso, setBaseCurrency, setDisplayFormats } from '../../../utils/format'
import { fmt } from './assetsData'
import { CorrectValueDialog, UpdateValueDialog } from './ValueDialogs'

const { authFetch } = vi.hoisted(() => ({ authFetch: vi.fn() }))
vi.mock('../../../api/client', () => ({ authFetch }))

setBaseCurrency('SGD')

const MARK: AssetMark = {
  id: 'mk_value00001',
  kind: 'valuation',
  date: '2026-06-01',
  value: 850000,
  note: 'Bank valuation',
  by: 'you',
  created_at: '2026-06-01T00:00:00Z',
  edited_at: null,
}

function asset(overrides: Partial<OwnedAsset> = {}): OwnedAsset {
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
    value: 850000,
    valued_on: '2026-06-01',
    share_value: 850000,
    marks: [MARK],
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    ...overrides,
  }
}

function ok(data: unknown): Response {
  return { ok: true, status: 200, json: async () => data } as Response
}

function refused(status: number, body: unknown): Response {
  return { ok: false, status, json: async () => body } as Response
}

const BEFORE_PURCHASE = {
  detail: "A value can't be dated before the purchase (15 Jun 2019).",
  code: 'conflict',
  reason: 'before_purchase',
  field: 'date',
}

function sent(): { path: string; method: string; body: unknown }[] {
  return authFetch.mock.calls.map(([path, init]) => ({
    path: String(path),
    method: String((init as RequestInit).method),
    body: JSON.parse(String((init as RequestInit).body)),
  }))
}

const title = () => document.querySelector('.ui-modal__title')?.textContent
const subtitle = () => document.querySelector('.ui-modal__subtitle')?.textContent
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole('button', { name }))

/** A MoneyField commits on blur, as clicking the dialog's button would. */
function typeMoney(label: string | RegExp, text: string) {
  const input = screen.getByRole('textbox', { name: label })
  fireEvent.focus(input)
  fireEvent.change(input, { target: { value: text } })
  fireEvent.blur(input)
}

function dateField(): HTMLInputElement {
  return screen.getByLabelText('As of') as HTMLInputElement
}

// A block body: an arrow returning the spy would hand Vitest a teardown that calls it.
beforeEach(() => {
  authFetch.mockReset()
})
afterEach(cleanup)

describe('UpdateValueDialog', () => {
  function renderUpdate(a = asset()) {
    const props = { a, onClose: vi.fn(), onSaved: vi.fn() }
    render(<UpdateValueDialog {...props} />)
    return props
  }

  it('shows the current value for reference and a blank New value, dated today', () => {
    renderUpdate()
    expect(title()).toBe('Update value')
    expect(subtitle()).toBe('Test Condo')
    expect(screen.getByText(`Current value: ${fmt(850000)} · valued Jun 2026`)).toBeTruthy()
    expect(screen.getByRole('textbox', { name: 'New value' }).getAttribute('value')).toBe('')
    expect(dateField().value).toBe(localIso())
    expect(dateField().max).toBe(localIso())
    expect(dateField().min).toBe('2019-06-15')
    expect(
      screen.getByText('This is saved as a new dated value — the old one stays in history.'),
    ).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/\bmark\b/i)
  })

  it('records a new dated value with its note, then hands back its toast', async () => {
    authFetch.mockResolvedValueOnce(ok(asset()))
    const props = renderUpdate()
    typeMoney('New value', '900000')
    fireEvent.change(dateField(), { target: { value: '2026-09-01' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Note (optional)' }), {
      target: { value: 'Agent estimate' },
    })
    click('Update value')
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith('Value updated.'))
    expect(sent()).toEqual([
      {
        path: '/api/assets/oa_test000001/marks',
        method: 'POST',
        body: { value: 900000, date: '2026-09-01', note: 'Agent estimate' },
      },
    ])
  })

  it('refuses a blank or zero value and a future date, sending nothing', () => {
    renderUpdate()
    click('Update value')
    expect(screen.getByText('Enter a value greater than 0.')).toBeTruthy()
    typeMoney('New value', '0')
    click('Update value')
    expect(screen.getByText('Enter a value greater than 0.')).toBeTruthy()
    typeMoney('New value', '900000')
    fireEvent.change(dateField(), { target: { value: '2999-01-01' } })
    click('Update value')
    expect(screen.getByText("Pick a date that isn't in the future.")).toBeTruthy()
    expect(authFetch).not.toHaveBeenCalled()
  })

  it('typing a value clears "Enter a value greater than 0."', () => {
    renderUpdate()
    click('Update value')
    expect(screen.getByText('Enter a value greater than 0.')).toBeTruthy()
    typeMoney('New value', '900000')
    expect(screen.queryByText('Enter a value greater than 0.')).toBeNull()
  })

  it('409 before_purchase: stays open with the reason beside As of, nothing lost', async () => {
    authFetch.mockResolvedValueOnce(refused(409, BEFORE_PURCHASE))
    const props = renderUpdate()
    typeMoney('New value', '900000')
    fireEvent.change(dateField(), { target: { value: '2019-01-01' } })
    click('Update value')
    expect((await screen.findByRole('alert')).textContent).toBe(BEFORE_PURCHASE.detail)
    expect(dateField().getAttribute('aria-describedby')).toBe(
      screen.getByRole('alert').getAttribute('id'),
    )
    expect(props.onSaved).not.toHaveBeenCalled()
    expect(title()).toBe('Update value')
    expect(screen.getByRole('textbox', { name: 'New value' }).getAttribute('value')).toBe('900,000')
    expect(dateField().value).toBe('2019-01-01')
  })

  it('a double-click records one value; the button says Saving… meanwhile', async () => {
    let answer: (r: Response) => void = () => {}
    authFetch.mockReturnValueOnce(new Promise<Response>((resolve) => (answer = resolve)))
    const props = renderUpdate()
    typeMoney('New value', '900000')
    click('Update value')
    click(/Update value|Saving…/)
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeTruthy()
    answer(ok(asset()))
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledTimes(1))
    expect(authFetch).toHaveBeenCalledTimes(1)
  })

  it('renders for an imported asset with no value yet, without a reference line', () => {
    renderUpdate(asset({ value: null, valued_on: null, share_value: null, marks: [] }))
    expect(screen.queryByText(/Current value/)).toBeNull()
    expect(screen.getByRole('textbox', { name: 'New value' })).toBeTruthy()
  })
})

describe("Value dialogs — text stops at the server's caps", () => {
  it('a note at 300 characters', () => {
    render(<UpdateValueDialog a={asset()} onClose={vi.fn()} onSaved={vi.fn()} />)
    const note = screen.getByRole('textbox', { name: 'Note (optional)' }) as HTMLInputElement
    expect(note.maxLength).toBe(300)
  })
})

describe('Value dialogs — what the value field says about the asset', () => {
  const note = () => screen.getByRole('textbox', { name: 'Note (optional)' })
  const SHARED_HELP = /The whole asset's value — net worth counts your/

  it("suggests a note from where the asset's figure usually comes — a property's valuation", () => {
    render(<UpdateValueDialog a={asset()} onClose={vi.fn()} onSaved={vi.fn()} />)
    expect(note().getAttribute('placeholder')).toBe('e.g. Bank valuation')
  })

  it("and a watch's marketplace median", () => {
    render(
      <UpdateValueDialog a={asset({ category: 'watch' })} onClose={vi.fn()} onSaved={vi.fn()} />,
    )
    expect(note().getAttribute('placeholder')).toBe('e.g. Chrono24 median')
  })

  it("on a shared asset, asks for the whole asset's value — net worth counts the share", () => {
    render(
      <UpdateValueDialog
        a={asset({ share_pct: 50, share_value: 425000 })}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    )
    const value = screen.getByRole('textbox', { name: 'New value' })
    const help = screen.getByText("The whole asset's value — net worth counts your 50%.")
    expect(value.getAttribute('aria-describedby')).toBe(help.getAttribute('id'))
  })

  it('says the same when correcting a value, with the share as the page writes it', () => {
    render(
      <CorrectValueDialog
        a={asset({ share_pct: 33.5, share_value: 284750 })}
        mark={MARK}
        removable
        onRemove={vi.fn()}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    )
    expect(screen.getByText("The whole asset's value — net worth counts your 33.5%.")).toBeTruthy()
  })

  it('says nothing of shares when the asset is all yours', () => {
    render(<UpdateValueDialog a={asset()} onClose={vi.fn()} onSaved={vi.fn()} />)
    expect(screen.queryByText(SHARED_HELP)).toBeNull()
  })
})

describe('Value dialogs — an amount they cannot read is never sent', () => {
  it('Update value: "910 thousand" after a good 900000 records nothing', async () => {
    const props = { a: asset(), onClose: vi.fn(), onSaved: vi.fn() }
    render(<UpdateValueDialog {...props} />)
    typeMoney('New value', '900000')
    typeMoney('New value', '910 thousand')
    click('Update value')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(authFetch).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toBe('Enter an amount of 0 or more.')
  })

  it("Update value: a figure typed after an Enter is the one recorded, not the Enter's", async () => {
    authFetch.mockResolvedValueOnce(ok(asset()))
    const props = { a: asset(), onClose: vi.fn(), onSaved: vi.fn() }
    render(<UpdateValueDialog {...props} />)
    const field = screen.getByRole('textbox', { name: 'New value' })
    fireEvent.focus(field)
    fireEvent.change(field, { target: { value: '900000' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    fireEvent.change(field, { target: { value: '950000' } })
    fireEvent.blur(field)
    click('Update value')
    await waitFor(() => expect(props.onSaved).toHaveBeenCalled())
    expect(sent()[0]?.body).toMatchObject({ value: 950000 })
  })

  it('Update value: another currency typed in front is refused, never read as S$', async () => {
    const props = { a: asset(), onClose: vi.fn(), onSaved: vi.fn() }
    render(<UpdateValueDialog {...props} />)
    typeMoney('New value', '¥2,000,000')
    click('Update value')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(authFetch).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toBe('Enter an amount of 0 or more.')
  })

  it('Update value: "S$820,000" is read as 820000', async () => {
    authFetch.mockResolvedValueOnce(ok(asset()))
    const props = { a: asset(), onClose: vi.fn(), onSaved: vi.fn() }
    render(<UpdateValueDialog {...props} />)
    typeMoney('New value', 'S$820,000')
    click('Update value')
    await waitFor(() => expect(props.onSaved).toHaveBeenCalled())
    expect(sent()[0]?.body).toMatchObject({ value: 820000 })
  })

  it('Correct this value: "860 thousand" with a new note sends nothing — not the note alone', async () => {
    const props = {
      a: asset(),
      mark: MARK,
      removable: true,
      onRemove: vi.fn(),
      onClose: vi.fn(),
      onSaved: vi.fn(),
    }
    render(<CorrectValueDialog {...props} />)
    typeMoney('Value', '860 thousand')
    fireEvent.change(screen.getByRole('textbox', { name: 'Note (optional)' }), {
      target: { value: 'Re-appraised' },
    })
    click('Save correction')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(authFetch).not.toHaveBeenCalled()
    expect(props.onSaved).not.toHaveBeenCalled()
    expect(props.onClose).not.toHaveBeenCalled()
  })
})

describe("Value dialogs — amounts read in the user's own number format", () => {
  afterEach(() => {
    setDisplayFormats({})
  })

  function correct() {
    authFetch.mockResolvedValueOnce(ok(asset()))
    const props = {
      a: asset(),
      mark: MARK,
      removable: true,
      onRemove: vi.fn(),
      onClose: vi.fn(),
      onSaved: vi.fn(),
    }
    render(<CorrectValueDialog {...props} />)
    return { props, input: screen.getByRole('textbox', { name: 'Value' }) }
  }

  it('dot format: opens on "850.000" and, edited in place to "860.000", sends 860000', async () => {
    setDisplayFormats({ number_format: 'dot' })
    const { props, input } = correct()
    expect(input.getAttribute('value')).toBe('850.000')
    fireEvent.change(input, { target: { value: '860.000' } })
    fireEvent.blur(input)
    click('Save correction')
    await waitFor(() => expect(props.onSaved).toHaveBeenCalled())
    expect(sent()[0]?.body).toEqual({ value: 860000 })
  })

  it('comma format: "860,000" sends 860000', async () => {
    setDisplayFormats({ number_format: 'comma' })
    const { props, input } = correct()
    expect(input.getAttribute('value')).toBe('850,000')
    fireEvent.change(input, { target: { value: '860,000' } })
    fireEvent.blur(input)
    click('Save correction')
    await waitFor(() => expect(props.onSaved).toHaveBeenCalled())
    expect(sent()[0]?.body).toEqual({ value: 860000 })
  })
})

describe('CorrectValueDialog', () => {
  function renderCorrect(mark: AssetMark = MARK, removable = true) {
    const props = {
      a: asset(),
      mark,
      removable,
      onRemove: vi.fn(),
      onClose: vi.fn(),
      onSaved: vi.fn(),
    }
    render(<CorrectValueDialog {...props} />)
    return props
  }

  it("offers Remove value on the footer's left, apart from Cancel and Save correction", () => {
    const props = renderCorrect()
    const footer = document.querySelector('.ui-modal__footer')!
    const buttons = Array.from(footer.querySelectorAll('button')).map((b) => b.textContent)
    expect(buttons).toEqual(['Remove value', 'Cancel', 'Save correction'])
    for (const b of footer.querySelectorAll('button')) expect(b.className).toContain('ui-btn--sm')
    fireEvent.click(screen.getByRole('button', { name: 'Remove value' }))
    expect(props.onRemove).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('An asset needs at least one value.')).toBeNull()
  })

  it("can't remove the last value — the button is off and says why", () => {
    const props = renderCorrect(MARK, false)
    const remove = screen.getByRole('button', { name: 'Remove value' }) as HTMLButtonElement
    expect(remove.disabled).toBe(true)
    fireEvent.click(remove)
    expect(props.onRemove).not.toHaveBeenCalled()
    expect(screen.getByText('An asset needs at least one value.')).toBeTruthy()
  })

  it('is prefilled from the row, with no reference line and no new-value hint', () => {
    renderCorrect()
    expect(title()).toBe('Correct this value')
    expect(subtitle()).toBe('Test Condo')
    // Focused on open, so it shows raw digits until it blurs.
    expect(
      screen.getByRole('textbox', { name: 'Value' }).getAttribute('value')?.replace(/,/g, ''),
    ).toBe('850000')
    expect(dateField().value).toBe('2026-06-01')
    expect(
      (screen.getByRole('textbox', { name: 'Note (optional)' }) as HTMLInputElement).value,
    ).toBe('Bank valuation')
    expect(screen.queryByText(/Current value/)).toBeNull()
    expect(screen.queryByText(/saved as a new dated value/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Save correction' })).toBeTruthy()
  })

  it('sends only what changed, to that value, and hands back its toast', async () => {
    authFetch.mockResolvedValueOnce(ok(asset()))
    const props = renderCorrect()
    typeMoney('Value', '805000')
    click('Save correction')
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith('Value corrected.'))
    expect(sent()).toEqual([
      {
        path: '/api/assets/oa_test000001/marks/mk_value00001',
        method: 'PATCH',
        body: { value: 805000 },
      },
    ])
  })

  it('closes without a request when nothing changed', () => {
    const props = renderCorrect()
    click('Save correction')
    expect(authFetch).not.toHaveBeenCalled()
    expect(props.onClose).toHaveBeenCalled()
    expect(props.onSaved).not.toHaveBeenCalled()
  })

  it('409 before_purchase: stays open with the reason beside As of', async () => {
    authFetch.mockResolvedValueOnce(refused(409, BEFORE_PURCHASE))
    const props = renderCorrect()
    fireEvent.change(dateField(), { target: { value: '2019-01-01' } })
    click('Save correction')
    expect((await screen.findByRole('alert')).textContent).toBe(BEFORE_PURCHASE.detail)
    expect(sent()[0]?.body).toEqual({ date: '2019-01-01' })
    expect(props.onSaved).not.toHaveBeenCalled()
    expect(title()).toBe('Correct this value')
    expect(dateField().value).toBe('2019-01-01')
  })

  it('a refusal naming no field is said in the dialog', async () => {
    authFetch.mockResolvedValueOnce(
      refused(404, { detail: 'That value no longer exists.', code: 'not_found' }),
    )
    renderCorrect()
    typeMoney('Value', '805000')
    click('Save correction')
    expect((await screen.findByRole('alert')).textContent).toBe('That value no longer exists.')
    expect(title()).toBe('Correct this value')
  })
})
