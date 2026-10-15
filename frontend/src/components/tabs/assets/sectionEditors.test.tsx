// @vitest-environment jsdom
//
// The asset page's section editors against a mocked authFetch — the real api.ts parses every
// answer, so a refusal is exactly what the server would send (RFC §3.1). Each editor must send
// exactly its RFC §3.4 keys, wait for the server, and keep what was typed when refused.
// Synthetic fixtures only; dialogs render through a Radix portal, so queries use `screen`.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { AssetCategory, AssetLoan, AssetMark, OwnedAsset } from '../../../types/assets'
import { localIso, setBaseCurrency, setDisplayFormats } from '../../../utils/format'
import { fmt } from './assetsData'
import { DetailsEditor } from './DetailsEditor'
import { OwnershipEditor } from './OwnershipEditor'
import { PurchaseEditor } from './PurchaseEditor'
import { RenameCategoryEditor } from './RenameCategoryEditor'
import { NotesEditor } from './sections'

const { authFetch } = vi.hoisted(() => ({ authFetch: vi.fn() }))
vi.mock('../../../api/client', () => ({ authFetch }))

setBaseCurrency('SGD')

const CATS: AssetCategory[] = [
  {
    key: 'property',
    label: 'Property',
    plural: 'Property',
    color: 'var(--cat-property)',
    custom: false,
  },
  {
    key: 'vehicle',
    label: 'Vehicle',
    plural: 'Vehicles',
    color: 'var(--cat-vehicle)',
    custom: false,
  },
  {
    key: 'policy',
    label: 'Insurance & endowments',
    plural: 'Insurance & endowments',
    color: 'var(--cat-policy)',
    custom: false,
  },
]

const MORTGAGE: AssetLoan = {
  id: 'acc_mortgage1',
  name: 'Test Mortgage',
  balance: 310000,
  rate_label: '',
  term_label: '',
  held_by: null,
}

function valuation(id: string, date: string, value: number): AssetMark {
  return {
    id,
    kind: 'valuation',
    date,
    value,
    note: '',
    by: 'you',
    created_at: `${date}T00:00:00Z`,
    edited_at: null,
  }
}

function asset(overrides: Partial<OwnedAsset> = {}): OwnedAsset {
  return {
    id: 'oa_test000001',
    name: 'Test Condo',
    category: 'property',
    description: 'Two-bedroom',
    photo_ref: null,
    facts: [
      ['Tenure', 'Freehold'],
      ['Parking', 'Lot 12'],
    ],
    notes: 'Keys with the agent.',
    cost: 620000,
    cost_date: '2019-06-15',
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: 850000,
    valued_on: '2026-09-01',
    share_value: 850000,
    marks: [valuation('mk_new', '2026-09-01', 850000), valuation('mk_first', '2021-03-10', 700000)],
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2021-03-10T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
    ...overrides,
  }
}

function ok(data: unknown): Response {
  return { ok: true, status: 200, json: async () => data } as Response
}

function refused(status: number, body: unknown): Response {
  return { ok: false, status, json: async () => body } as Response
}

/** Answers each path from its own queue; anything unexpected fails loudly. */
function serve(queues: Record<string, Response[]>) {
  authFetch.mockImplementation(async (path: string) => {
    const next = queues[path]?.shift()
    if (!next) throw new Error(`unexpected request to ${path}`)
    return next
  })
}

function bodies(path: string): Record<string, unknown>[] {
  return authFetch.mock.calls
    .filter(([p]) => p === path)
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)) as Record<string, unknown>)
}

const PATCH = '/api/assets/oa_test000001'
/** Every PATCH's `changes`. */
const changes = () => bodies(PATCH).map((b) => b.changes as Record<string, unknown>)

const title = () => document.querySelector('.ui-modal__title')?.textContent
const subtitle = () => document.querySelector('.ui-modal__subtitle')?.textContent
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole('button', { name }))
const type = (label: string | RegExp, text: string) =>
  fireEvent.change(screen.getByRole('textbox', { name: label }), { target: { value: text } })

function typeMoney(label: string | RegExp, text: string) {
  const input = screen.getByRole('textbox', { name: label })
  fireEvent.focus(input)
  fireEvent.change(input, { target: { value: text } })
  fireEvent.blur(input)
}

function handlers() {
  return { onClose: vi.fn(), onSaved: vi.fn() }
}

// A block body: an arrow returning the spy would hand Vitest a teardown that calls it.
beforeEach(() => {
  authFetch.mockReset()
})
afterEach(cleanup)

describe('RenameCategoryEditor', () => {
  it('sends exactly name, description and category, then says so — not "Details saved."', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<RenameCategoryEditor a={asset()} cats={CATS} {...h} />)
    expect(title()).toBe('Rename or change category')
    expect(subtitle()).toBe('Test Condo')
    // Owner 2026-09-28: the "line under the name in the register" hint didn't make sense.
    expect(screen.queryByText(/under the name/)).toBeNull()
    type(/^Name/, 'Test Loft')
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalledWith('Name and category saved.'))
    expect(changes()).toEqual([
      { name: 'Test Loft', description: 'Two-bedroom', category: 'property' },
    ])
  })

  it('a category change says what moves and keeps every detail (facts are not sent)', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<RenameCategoryEditor a={asset()} cats={CATS} {...h} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Category' }), {
      target: { value: 'vehicle' },
    })
    expect(document.querySelector('.as-hint')?.textContent).toBe(
      'Moving this to Vehicle changes the suggested details. Its values and documents stay with it.',
    )
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalled())
    expect(changes()[0]).toEqual({
      name: 'Test Condo',
      description: 'Two-bedroom',
      category: 'vehicle',
    })
  })

  it('needs a name, and keeps a refused one with the reason beside it', async () => {
    serve({
      [PATCH]: [
        refused(422, {
          detail: 'Give it a name (up to 120 characters).',
          code: 'validation',
          field: 'name',
        }),
      ],
    })
    const h = handlers()
    render(<RenameCategoryEditor a={asset()} cats={CATS} {...h} />)
    type(/^Name/, '  ')
    click('Save changes')
    expect(screen.getByRole('alert').textContent).toBe('Enter a name.')
    expect(authFetch).not.toHaveBeenCalled()
    type(/^Name/, 'X'.repeat(130))
    click('Save changes')
    expect((await screen.findByRole('alert')).textContent).toBe(
      'Give it a name (up to 120 characters).',
    )
    expect(title()).toBe('Rename or change category')
    expect((screen.getByRole('textbox', { name: /^Name/ }) as HTMLInputElement).value).toHaveLength(
      130,
    )
    expect(h.onSaved).not.toHaveBeenCalled()
  })
})

describe('OwnershipEditor', () => {
  it('is the wizard step in a dialog, and sends exactly share_pct, share_note and loan_account_id', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<OwnershipEditor a={asset()} loans={[MORTGAGE]} {...h} />)
    expect(title()).toBe('Ownership')
    expect(subtitle()).toBe('Test Condo')
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    type('Shared with (optional)', ' Joint with A. Lim ')
    fireEvent.change(screen.getByRole('combobox', { name: 'Any loan against it?' }), {
      target: { value: MORTGAGE.id },
    })
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalledWith('Ownership updated.'))
    expect(changes()).toEqual([
      { share_pct: 50, share_note: 'Joint with A. Lim', loan_account_id: MORTGAGE.id },
    ])
  })

  it('All mine clears the share and who with; No loan unlinks', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    const shared = asset({ share_pct: 60, share_note: 'Joint', loan_account_id: MORTGAGE.id })
    render(<OwnershipEditor a={shared} loans={[MORTGAGE]} {...h} />)
    fireEvent.click(screen.getByRole('tab', { name: 'All mine' }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Any loan against it?' }), {
      target: { value: '' },
    })
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalled())
    expect(changes()).toEqual([{ share_pct: 100, share_note: '', loan_account_id: null }])
  })

  it('creates a staged loan only on save, just before the asset; a refused save keeps it by its real id', async () => {
    const loan = { id: 'acc_newloan1', name: 'Test Car Loan', balance: -38000 }
    serve({
      '/api/accounts': [ok(loan)],
      [PATCH]: [
        refused(409, {
          detail:
            'Test Car Loan already secures “Test Car”. A loan can secure one asset — unlink it there first.',
          code: 'conflict',
          reason: 'loan_in_use',
          field: 'loan_account_id',
          holder: { id: 'oa_car0000001', name: 'Test Car' },
        }),
        ok(asset()),
      ],
    })
    const h = handlers()
    render(<OwnershipEditor a={asset()} loans={[]} {...h} />)
    click('Add a loan')
    type('Loan name', 'Test Car Loan')
    typeMoney('Amount owed', '38000')
    click('Add loan')
    expect(screen.getByText('Added to Accounts when you save.')).toBeTruthy()
    expect(authFetch).not.toHaveBeenCalled()

    click('Save changes')
    expect((await screen.findByRole('alert')).textContent).toContain('already secures')
    expect(bodies('/api/accounts')).toEqual([
      { name: 'Test Car Loan', type: 'loan', initial_balance: -38000 },
    ])
    const select = screen.getByRole('combobox', {
      name: 'Any loan against it?',
    }) as HTMLSelectElement
    expect(select.value).toBe('acc_newloan1')
    expect(select.selectedOptions[0]?.textContent).toBe(`Test Car Loan · ${fmt(38000)}`)
    expect(h.onSaved).not.toHaveBeenCalled()

    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalledWith('Ownership updated.'))
    expect(bodies('/api/accounts')).toHaveLength(1)
    expect(changes().map((c) => c.loan_account_id)).toEqual(['acc_newloan1', 'acc_newloan1'])
  })

  it('a refused staged loan is said beside its own field, and no asset write is made', async () => {
    serve({
      '/api/accounts': [
        refused(422, { detail: 'That account name is already taken.', field: 'name' }),
      ],
    })
    render(<OwnershipEditor a={asset()} loans={[]} {...handlers()} />)
    click('Add a loan')
    type('Loan name', 'Test Car Loan')
    typeMoney('Amount owed', '38000')
    click('Add loan')
    click('Save changes')
    expect((await screen.findByRole('alert')).textContent).toBe(
      'That account name is already taken.',
    )
    expect(screen.getByRole('textbox', { name: 'Loan name' })).toBeTruthy()
    expect(bodies(PATCH)).toEqual([])
  })

  it('refuses a share outside 0–100 before sending anything', () => {
    render(<OwnershipEditor a={asset()} loans={[]} {...handlers()} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    typeMoney('Your share', '150')
    click('Save changes')
    expect(screen.getByRole('alert').textContent).toBe('Enter a share from 0 to 100.')
    expect(authFetch).not.toHaveBeenCalled()
  })
})

describe('PurchaseEditor', () => {
  it('is the wizard step in a dialog, bounded by the first value, and sends exactly cost and cost_date', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<PurchaseEditor a={asset()} {...h} />)
    expect(title()).toBe('Purchase')
    expect(subtitle()).toBe('Test Condo')
    // Editing what was paid is not optional in the way adding it was.
    expect(screen.getByText('Adds your gain and yearly return.')).toBeTruthy()
    const date = screen.getByLabelText('Bought on') as HTMLInputElement
    expect(date.max).toBe('2021-03-10')
    typeMoney('Bought for', '600000')
    fireEvent.change(date, { target: { value: '2020-01-02' } })
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalledWith('Purchase updated.'))
    expect(changes()).toEqual([{ cost: 600000, cost_date: '2020-01-02' }])
  })

  it('clears both with null', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<PurchaseEditor a={asset()} {...h} />)
    typeMoney('Bought for', '')
    fireEvent.change(screen.getByLabelText('Bought on'), { target: { value: '' } })
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalled())
    expect(changes()).toEqual([{ cost: null, cost_date: null }])
  })

  it('409 after_first_value: stays open with the reason beside the date', async () => {
    const detail =
      "The purchase date can't be after 10 Mar 2021, the first value you recorded. Record a value on or after the purchase date, then remove the older one."
    serve({
      [PATCH]: [
        refused(409, { detail, code: 'conflict', reason: 'after_first_value', field: 'cost_date' }),
      ],
    })
    const h = handlers()
    render(<PurchaseEditor a={asset()} {...h} />)
    fireEvent.change(screen.getByLabelText('Bought on'), { target: { value: '2021-05-01' } })
    click('Save changes')
    expect((await screen.findByRole('alert')).textContent).toBe(detail)
    expect((screen.getByLabelText('Bought on') as HTMLInputElement).value).toBe('2021-05-01')
    expect(h.onSaved).not.toHaveBeenCalled()
  })

  it('is "Premiums paid" for a policy, bounded by today when there is no value', () => {
    render(<PurchaseEditor a={asset({ category: 'policy', marks: [] })} {...handlers()} />)
    expect(title()).toBe('Premiums paid')
    expect((screen.getByLabelText('Policy started on') as HTMLInputElement).max).toBe(localIso())
  })
})

describe('DetailsEditor', () => {
  it('guided fields first, other details after; sends exactly facts, insured and maturity_date', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<DetailsEditor a={asset()} {...h} />)
    expect(title()).toBe('Details')
    expect(subtitle()).toBe('Test Condo')
    expect((screen.getByRole('textbox', { name: 'Tenure' }) as HTMLInputElement).value).toBe(
      'Freehold',
    )
    expect(screen.getByRole('textbox', { name: 'Floor area' }).getAttribute('placeholder')).toBe(
      '1,184 sqft',
    )
    expect(screen.getByText('Other details')).toBeTruthy()
    expect(screen.getByDisplayValue('Parking')).toBeTruthy()
    type('Floor area', '1,184 sqft')
    fireEvent.click(screen.getByRole('switch', { name: /insured/i }))
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalledWith('Details saved.'))
    expect(changes()).toEqual([
      {
        facts: [
          ['Tenure', 'Freehold'],
          ['Floor area', '1,184 sqft'],
          ['Parking', 'Lot 12'],
        ],
        insured: true,
        maturity_date: null,
      },
    ])
  })

  it('adds and removes other details', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<DetailsEditor a={asset()} {...h} />)
    click('Remove this detail')
    click('Add a detail')
    const labels = screen.getAllByPlaceholderText('Label')
    const values = screen.getAllByPlaceholderText('Value')
    fireEvent.change(labels[labels.length - 1]!, { target: { value: 'View' } })
    fireEvent.change(values[values.length - 1]!, { target: { value: 'Sea' } })
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalled())
    expect(changes()[0]?.facts).toEqual([
      ['Tenure', 'Freehold'],
      ['View', 'Sea'],
    ])
  })

  it('refuses a detail with only a label or only a value, beside it — never drops it', async () => {
    render(<DetailsEditor a={asset({ facts: [] })} {...handlers()} />)
    click('Add a detail')
    click('Add a detail')
    const labels = screen.getAllByRole('textbox', { name: 'Detail label' })
    const values = screen.getAllByRole('textbox', { name: 'Detail value' })
    fireEvent.change(labels[0]!, { target: { value: 'Parking' } })
    fireEvent.change(values[1]!, { target: { value: 'Lot 12' } })
    click('Save changes')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(authFetch).not.toHaveBeenCalled()
    const said = () => screen.queryAllByRole('alert').map((n) => n.textContent)
    expect(said()).toEqual([
      'Add a value, or remove this detail.',
      'Add a label, or remove this detail.',
    ])
    expect(values[0]!.getAttribute('aria-invalid')).toBe('true')
    expect(labels[0]!.getAttribute('aria-invalid')).toBeNull()
    expect(labels[1]!.getAttribute('aria-invalid')).toBe('true')
    expect(values[0]!.getAttribute('aria-describedby')).toBe(
      screen.getAllByRole('alert')[0]!.getAttribute('id'),
    )
    // Fixing a row clears what was said beside it; removing one clears the rest (rows move).
    fireEvent.change(values[0]!, { target: { value: 'Lot 7' } })
    expect(said()).toEqual(['Add a label, or remove this detail.'])
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove this detail' })[0]!)
    expect(said()).toEqual([])
  })

  it('drops a detail row left wholly empty without a word', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<DetailsEditor a={asset()} {...h} />)
    click('Add a detail')
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalled())
    expect(changes()[0]?.facts).toEqual([
      ['Tenure', 'Freehold'],
      ['Parking', 'Lot 12'],
    ])
  })

  it('opened for a custom detail, a new row is there with the cursor in its label', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<DetailsEditor a={asset()} addDetail {...h} />)
    const labels = screen.getAllByRole('textbox', { name: 'Detail label' })
    expect(labels.map((l) => (l as HTMLInputElement).value)).toEqual(['Parking', ''])
    expect(document.activeElement).toBe(labels[1])
    fireEvent.change(labels[1]!, { target: { value: 'Bezel' } })
    fireEvent.change(screen.getAllByRole('textbox', { name: 'Detail value' })[1]!, {
      target: { value: 'Rose gold' },
    })
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalledWith('Details saved.'))
    expect(changes()[0]?.facts).toEqual([
      ['Tenure', 'Freehold'],
      ['Parking', 'Lot 12'],
      ['Bezel', 'Rose gold'],
    ])
  })

  it('opened for a custom detail and saved untouched, the empty row adds nothing', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<DetailsEditor a={asset()} addDetail {...h} />)
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalled())
    expect(screen.queryAllByRole('alert')).toEqual([])
    expect(changes()[0]?.facts).toEqual([
      ['Tenure', 'Freehold'],
      ['Parking', 'Lot 12'],
    ])
  })

  it('Add a detail puts the cursor in the new row', () => {
    render(<DetailsEditor a={asset()} {...handlers()} />)
    click('Add a detail')
    const labels = screen.getAllByRole('textbox', { name: 'Detail label' })
    expect(document.activeElement).toBe(labels[labels.length - 1])
  })

  it('a policy asks when it matures, never whether it is insured', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<DetailsEditor a={asset({ category: 'policy', facts: [], insured: false })} {...h} />)
    expect(screen.queryByRole('switch', { name: /insured/i })).toBeNull()
    fireEvent.change(screen.getByLabelText('Matures on'), { target: { value: '2041-06-01' } })
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalled())
    expect(changes()).toEqual([{ facts: [], insured: false, maturity_date: '2041-06-01' }])
  })

  it('stays open with the reason when refused', async () => {
    serve({ [PATCH]: [refused(503, { detail: 'The server is busy. Try again.' })] })
    const h = handlers()
    render(<DetailsEditor a={asset()} {...h} />)
    type('Tenure', '99-year')
    click('Save changes')
    expect((await screen.findByRole('alert')).textContent).toBe('The server is busy. Try again.')
    expect((screen.getByRole('textbox', { name: 'Tenure' }) as HTMLInputElement).value).toBe(
      '99-year',
    )
    expect(h.onSaved).not.toHaveBeenCalled()
  })
})

describe('NotesEditor', () => {
  it('sends exactly the notes, then says Note saved.', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<NotesEditor a={asset()} {...h} />)
    expect(title()).toBe('Notes')
    expect(subtitle()).toBe('Test Condo')
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Receipt in the safe.' } })
    click('Save')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalledWith('Note saved.'))
    expect(changes()).toEqual([{ notes: 'Receipt in the safe.' }])
  })

  it('gives a note room to be read and written — never the small dialog', () => {
    // Owner 2026-09-28: "why is the modal for the notes too small?"
    render(<NotesEditor a={asset()} {...handlers()} />)
    expect(document.querySelector('.ui-modal-card--lg')).toBeTruthy()
    expect(screen.getByRole('textbox', { name: 'Notes' }).getAttribute('rows')).toBe('12')
  })
})

// A browser's autofill, or a script's fill(), fires several fields' events in one task. Each
// field must merge into the editor's newest value, never rebuild it from a last-rendered copy.
describe('Section editors — same-tick field updates', () => {
  function draftMoney(label: string | RegExp, text: string): HTMLElement {
    const input = screen.getByRole('textbox', { name: label })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: text } })
    return input
  }

  it('Purchase keeps a price committed in the same tick as its date', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<PurchaseEditor a={asset()} {...h} />)
    const price = draftMoney('Bought for', '600000')
    act(() => {
      fireEvent.blur(price)
      fireEvent.change(screen.getByLabelText('Bought on'), { target: { value: '2020-01-02' } })
    })
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalled())
    expect(changes()).toEqual([{ cost: 600000, cost_date: '2020-01-02' }])
  })

  it('Ownership keeps a share committed in the same tick as who it is shared with', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<OwnershipEditor a={asset()} loans={[MORTGAGE]} {...h} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    const share = draftMoney('Your share', '60')
    act(() => {
      fireEvent.blur(share)
      type('Shared with (optional)', 'Joint with A. Lim')
    })
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalled())
    expect(changes()).toEqual([
      { share_pct: 60, share_note: 'Joint with A. Lim', loan_account_id: null },
    ])
  })

  it('Details keeps every field an autofill fills at once', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<DetailsEditor a={asset()} {...h} />)
    act(() => {
      type('Address or unit', '#14-02, 1 Test Rise')
      type('Floor area', '1,184 sqft')
      fireEvent.change(screen.getByDisplayValue('Parking'), { target: { value: 'Car park' } })
      fireEvent.change(screen.getByDisplayValue('Lot 12'), { target: { value: 'Lot 14' } })
    })
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalled())
    expect(changes()[0]?.facts).toEqual([
      ['Address or unit', '#14-02, 1 Test Rise'],
      ['Tenure', 'Freehold'],
      ['Floor area', '1,184 sqft'],
      ['Car park', 'Lot 14'],
    ])
  })
})

describe('Section editors — an error leaves once its field is fixed', () => {
  it('Rename: typing a name clears "Enter a name." and its red border', () => {
    render(<RenameCategoryEditor a={asset()} cats={CATS} {...handlers()} />)
    type(/^Name/, ' ')
    click('Save changes')
    const name = screen.getByRole('textbox', { name: /^Name/ })
    expect(screen.getByText('Enter a name.')).toBeTruthy()
    expect(name.classList.contains('ui-input--invalid')).toBe(true)
    type(/^Name/, 'Test Loft')
    expect(screen.queryByText('Enter a name.')).toBeNull()
    expect(name.classList.contains('ui-input--invalid')).toBe(false)
  })

  it("Purchase: moving the date clears the server's refusal beside it", async () => {
    const detail = "The purchase date can't be after 10 Mar 2021, the first value you recorded."
    serve({
      [PATCH]: [
        refused(409, { detail, code: 'conflict', reason: 'after_first_value', field: 'cost_date' }),
      ],
    })
    render(<PurchaseEditor a={asset()} {...handlers()} />)
    fireEvent.change(screen.getByLabelText('Bought on'), { target: { value: '2021-05-01' } })
    click('Save changes')
    expect((await screen.findByRole('alert')).textContent).toBe(detail)
    fireEvent.change(screen.getByLabelText('Bought on'), { target: { value: '2020-05-01' } })
    expect(screen.queryByText(detail)).toBeNull()
  })
})

describe('Section editors — an amount they cannot read is never sent', () => {
  it('Purchase: unreadable "Bought for" sends nothing', async () => {
    render(<PurchaseEditor a={asset()} {...handlers()} />)
    typeMoney('Bought for', 'six hundred k')
    click('Save changes')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(authFetch).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toBe('Enter an amount of 0 or more.')
  })

  it('Purchase: "S$600,000" is read as 600000', async () => {
    serve({ [PATCH]: [ok(asset())] })
    const h = handlers()
    render(<PurchaseEditor a={asset()} {...h} />)
    typeMoney('Bought for', 'S$600,000')
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalled())
    expect(changes()[0]).toMatchObject({ cost: 600000 })
  })

  it('Purchase, dot format: opens on "620.000" and, edited in place to "600.000", sends 600000', async () => {
    setDisplayFormats({ number_format: 'dot' })
    try {
      serve({ [PATCH]: [ok(asset())] })
      const h = handlers()
      render(<PurchaseEditor a={asset()} {...h} />)
      const input = screen.getByRole('textbox', { name: 'Bought for' })
      expect(input.getAttribute('value')).toBe('620.000')
      fireEvent.change(input, { target: { value: '600.000' } })
      fireEvent.blur(input)
      click('Save changes')
      await waitFor(() => expect(h.onSaved).toHaveBeenCalled())
      expect(changes()[0]).toMatchObject({ cost: 600000 })
    } finally {
      setDisplayFormats({})
    }
  })

  it('Ownership: a share of "33,3" is refused, never saved as the default 50', async () => {
    render(<OwnershipEditor a={asset()} loans={[]} {...handlers()} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    typeMoney('Your share', '33,3')
    click('Save changes')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(authFetch).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toBe('Enter an amount of 0 or more.')
  })
})

describe("Section editors — text stops at the server's caps", () => {
  const cap = (el: HTMLElement) => (el as HTMLInputElement).maxLength

  it('Rename: a name of 120, a description of 300', () => {
    render(<RenameCategoryEditor a={asset()} cats={CATS} {...handlers()} />)
    expect(cap(screen.getByRole('textbox', { name: /^Name/ }))).toBe(120)
    expect(cap(screen.getByRole('textbox', { name: 'Short description' }))).toBe(300)
  })

  it('Details: every guided and other detail at 120', () => {
    render(<DetailsEditor a={asset()} {...handlers()} />)
    const fields = [
      screen.getByRole('textbox', { name: 'Tenure' }),
      ...screen.getAllByRole('textbox', { name: /^Detail (label|value)$/ }),
    ]
    expect(fields.map(cap)).toEqual([120, 120, 120])
  })

  it('Ownership: who it is shared with at 200; Notes at 4000', () => {
    const { unmount } = render(<OwnershipEditor a={asset()} loans={[]} {...handlers()} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    expect(cap(screen.getByRole('textbox', { name: 'Shared with (optional)' }))).toBe(200)
    unmount()
    render(<NotesEditor a={asset()} {...handlers()} />)
    expect(cap(screen.getByRole('textbox', { name: 'Notes' }))).toBe(4000)
  })
})

// Review R13: the Ownership editor's Save changes waits for an open "Add a loan" form the same
// way — nothing sent, the reason beside the form, focus taken to it.
describe("OwnershipEditor — an open loan form holds Save changes until it's added or cancelled", () => {
  const HOLD = 'Add or cancel this loan first.'
  const TEST_LOAN = { id: 'acc_newloan1', name: 'Test Car Loan', balance: -38000 }
  const loanForm = () => screen.queryByRole('group', { name: 'New loan account' })

  function openEditor() {
    const h = handlers()
    render(<OwnershipEditor a={asset()} loans={[]} {...h} />)
    return h
  }

  /** A loan staged at 38,000, its form open again ("Edit new loan"). */
  function reopenStagedLoan() {
    const h = openEditor()
    click('Add a loan')
    type('Loan name', 'Test Car Loan')
    typeMoney('Amount owed', '38000')
    click('Add loan')
    click('Edit new loan')
    return h
  }

  async function expectHeld(h: ReturnType<typeof handlers>) {
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(authFetch).not.toHaveBeenCalled()
    expect(h.onSaved).not.toHaveBeenCalled()
    const said = screen.getByText(HOLD)
    expect(said.getAttribute('role')).toBe('alert')
    expect(loanForm()?.contains(said)).toBe(true)
    expect(document.activeElement).toBe(loanForm())
  }

  it.each([
    ["an amount it can't read", '4O,000'],
    ['a figure typed but not added', '40000'],
  ])('holds it over %s — no loan, no asset write, no toast', async (_, text) => {
    const h = reopenStagedLoan()
    typeMoney('Amount owed', text)
    click('Save changes')
    await expectHeld(h)
  })

  it('holds it over a new loan typed but never added', async () => {
    const h = openEditor()
    click('Add a loan')
    type('Loan name', 'Test Car Loan')
    click('Save changes')
    await expectHeld(h)
  })

  it('lets an untouched open form go — it closes as if cancelled, the staged loan kept', async () => {
    serve({ '/api/accounts': [ok(TEST_LOAN)], [PATCH]: [ok(asset())] })
    const h = reopenStagedLoan()
    click('Save changes')
    await waitFor(() => expect(h.onSaved).toHaveBeenCalledWith('Ownership updated.'))
    expect(bodies('/api/accounts')).toEqual([
      { name: 'Test Car Loan', type: 'loan', initial_balance: -38000 },
    ])
    expect(screen.queryByText('New loan account')).toBeNull()
  })
})
