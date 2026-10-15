// @vitest-environment jsdom
//
// The Add asset wizard end to end against a mocked authFetch — the real api.ts parses every
// response, so a refusal here is exactly what the server would send (RFC §3.1). Synthetic
// fixtures only. The dialog renders through a Radix portal: every query goes through `screen`.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { AssetCategory, AssetLoan } from '../../../types/assets'
import { fNum, localIso, setBaseCurrency } from '../../../utils/format'
import { NEW_LOAN } from './assetForms'
import { fmt } from './assetsData'
import { AssetWizard, type AssetWizardProps } from './AssetWizard'

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
    key: 'watch',
    label: 'Watch & jewellery',
    plural: 'Watches & jewellery',
    color: 'var(--cat-watch)',
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

function ok(data: unknown): Response {
  return { ok: true, status: 200, json: async () => data } as Response
}

function refused(status: number, body: unknown): Response {
  return { ok: false, status, json: async () => body } as Response
}

const CREATED = { id: 'oa_new0001', name: 'Test Asset' }

/** Answers each POST path from its own queue; anything unexpected fails loudly. */
function serve(queues: Record<string, Response[]>) {
  authFetch.mockImplementation(async (path: string) => {
    const next = queues[path]?.shift()
    if (!next) throw new Error(`unexpected request to ${path}`)
    return next
  })
}

function posted(path: string): Record<string, unknown>[] {
  return authFetch.mock.calls
    .filter(([p]) => p === path)
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)) as Record<string, unknown>)
}

function renderWizard(overrides: Partial<AssetWizardProps> = {}) {
  const props: AssetWizardProps = {
    baseCurrency: 'SGD',
    cats: CATS,
    loans: [MORTGAGE],
    initialCategory: null,
    onClose: vi.fn(),
    onCreated: vi.fn(),
    onAskLucius: vi.fn(),
    ...overrides,
  }
  render(<AssetWizard {...props} />)
  return props
}

const title = () => document.querySelector('.ui-modal__title')?.textContent
const subtitle = () => document.querySelector('.ui-modal__subtitle')?.textContent
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole('button', { name }))

function type(label: string | RegExp, text: string) {
  fireEvent.change(screen.getByRole('textbox', { name: label }), { target: { value: text } })
}

/** The figure a money field holds — formatted at rest, raw digits while focused. */
function digits(label: string | RegExp): string {
  return (screen.getByRole('textbox', { name: label }).getAttribute('value') ?? '').replace(
    /\D/g,
    '',
  )
}

/** A MoneyField commits on blur, as clicking the next button would. */
function typeMoney(label: string | RegExp, text: string) {
  const input = screen.getByRole('textbox', { name: label })
  fireEvent.focus(input)
  fireEvent.change(input, { target: { value: text } })
  fireEvent.blur(input)
}

function fillNameValue(name = 'Test Asset', value = '14800') {
  type(/^Name/, name)
  typeMoney(/Worth today|Cash value today/, value)
}

beforeEach(() => {
  authFetch.mockReset()
})
afterEach(cleanup)

describe('AssetWizard — the flow', () => {
  it('adds a watch in four taps, each step led by its question, and hands the asset back', async () => {
    serve({ '/api/assets': [ok(CREATED)] })
    const props = renderWizard()
    // The bar is decorative — the subtitle says where you are in words.
    const bar = () => document.querySelector('.as-wizard-progress')
    const filled = () => document.querySelectorAll('.as-wizard-progress > span.on').length
    expect(title()).toBe('What is it?')
    expect(subtitle()).toBe('Step 1 of 4')
    expect(bar()?.getAttribute('aria-hidden')).toBe('true')
    expect(filled()).toBe(1)

    click('Watch & jewellery') // tap 1
    expect(title()).toBe('Name and value')
    expect(subtitle()).toBe('Watch & jewellery · Step 2 of 4')
    expect(filled()).toBe(2)
    fillNameValue()
    click('Continue') // tap 2
    expect(title()).toBe('Is it all yours?')
    expect(subtitle()).toBe('Watch & jewellery · Step 3 of 4')
    expect(filled()).toBe(3)
    expect(screen.queryByText('Any loan against it?')).toBeNull()
    click('Continue') // tap 3
    expect(title()).toBe('How much did you pay?')
    expect(subtitle()).toBe('Watch & jewellery · Step 4 of 4')
    expect(filled()).toBe(4)
    expect(screen.getByText('Optional — adds your gain and yearly return.')).toBeTruthy()
    click('Add asset') // tap 4

    await waitFor(() => expect(props.onCreated).toHaveBeenCalledWith(CREATED))
    expect(posted('/api/assets')).toEqual([
      { name: 'Test Asset', category: 'watch', value: 14800, share_pct: 100 },
    ])
  })

  it('adds a half-owned property with its loan and what was paid', async () => {
    serve({ '/api/assets': [ok(CREATED)] })
    const props = renderWizard()
    click('Property')
    fillNameValue('Test Condo', '850000')
    click('Continue')
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    type('Shared with (optional)', 'Joint with A. Lim')
    fireEvent.change(screen.getByRole('combobox', { name: 'Any loan against it?' }), {
      target: { value: MORTGAGE.id },
    })
    click('Continue')
    typeMoney('Bought for', '620000')
    fireEvent.change(screen.getByLabelText('Bought on'), { target: { value: '2019-06-15' } })
    click('Add asset')

    await waitFor(() => expect(props.onCreated).toHaveBeenCalled())
    expect(posted('/api/assets')).toEqual([
      {
        name: 'Test Condo',
        category: 'property',
        value: 850000,
        share_pct: 50,
        share_note: 'Joint with A. Lim',
        loan_account_id: MORTGAGE.id,
        cost: 620000,
        cost_date: '2019-06-15',
      },
    ])
  })

  it('words a policy as cash value and premiums, and saves them as value, cost and date', async () => {
    serve({ '/api/assets': [ok(CREATED)] })
    renderWizard()
    click('Insurance & endowments')
    expect(subtitle()).toBe('Insurance & endowments · Step 2 of 4')
    expect(
      screen.getByText('The surrender value on your latest statement — not the sum assured.'),
    ).toBeTruthy()
    fillNameValue('Test Endowment', '42000')
    click('Continue')
    click('Continue')
    expect(title()).toBe('How much have you paid in?')
    expect(
      screen.getByText("Optional. Shows whether the cash value is above what you've paid."),
    ).toBeTruthy()
    typeMoney('Premiums paid so far', '18000')
    fireEvent.change(screen.getByLabelText('Policy started on'), {
      target: { value: '2019-06-01' },
    })
    click('Add asset')
    await waitFor(() => expect(posted('/api/assets')).toHaveLength(1))
    expect(posted('/api/assets')[0]).toMatchObject({
      category: 'policy',
      value: 42000,
      cost: 18000,
      cost_date: '2019-06-01',
    })
  })

  it('starts at step 2 when a category chip is active; Back returns to change it', () => {
    renderWizard({ initialCategory: 'vehicle' })
    expect(subtitle()).toBe('Vehicle · Step 2 of 4')
    expect(title()).toBe('Name and value')
    click('Back')
    expect(title()).toBe('What is it?')
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()
  })

  it('goes Back without losing anything typed', () => {
    renderWizard()
    click('Property')
    fillNameValue('Test Condo', '850000')
    click('Continue')
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    click('Continue')
    typeMoney('Bought for', '620000')
    click('Back')
    expect(screen.getByRole('tab', { name: 'Shared' }).getAttribute('aria-selected')).toBe('true')
    click('Back')
    expect((screen.getByRole('textbox', { name: /^Name/ }) as HTMLInputElement).value).toBe(
      'Test Condo',
    )
    expect(screen.getByRole('textbox', { name: /Worth today/ }).getAttribute('value')).toBe(
      fNum(850000),
    )
    click('Continue')
    click('Continue')
    expect(digits('Bought for')).toBe('620000')
  })

  it('advances with Enter from a field, the same as Continue', () => {
    renderWizard({ initialCategory: 'watch' })
    type(/^Name/, 'Test Watch')
    const value = screen.getByRole('textbox', { name: /Worth today/ })
    fireEvent.focus(value)
    fireEvent.change(value, { target: { value: '14800' } })
    fireEvent.keyDown(value, { key: 'Enter' })
    expect(title()).toBe('Is it all yours?')
  })

  it('creates a category from step 1 and carries on with it', async () => {
    serve({
      '/api/assets/categories': [
        ok({
          key: 'c_boats',
          label: 'Boats',
          plural: 'Boats',
          color: 'var(--cat-other)',
          custom: true,
        }),
      ],
      '/api/assets': [ok(CREATED)],
    })
    renderWizard()
    click('New category')
    fireEvent.change(screen.getByPlaceholderText('e.g. Boats, Wine collection'), {
      target: { value: 'Boats' },
    })
    click('Create')
    await waitFor(() => expect(subtitle()).toBe('Boats · Step 2 of 4'))
    fillNameValue('Test Boat', '30000')
    click('Continue')
    // A custom category may be financed, so it asks about a loan.
    expect(screen.getByRole('combobox', { name: 'Any loan against it?' })).toBeTruthy()
    click('Continue')
    click('Add asset')
    await waitFor(() => expect(posted('/api/assets')).toHaveLength(1))
    expect(posted('/api/assets')[0]).toMatchObject({ category: 'c_boats', name: 'Test Boat' })
  })

  it('hands the job to Lucius from step 1', () => {
    const props = renderWizard()
    click('Describe it to Lucius')
    expect(props.onClose).toHaveBeenCalled()
    expect(props.onAskLucius).toHaveBeenCalledWith('Add an asset: ')
  })
})

describe('AssetWizard — validation', () => {
  it('holds step 2 until there is a name and a value above 0', () => {
    renderWizard({ initialCategory: 'watch' })
    click('Continue')
    expect(screen.getByText('Enter a name.')).toBeTruthy()
    expect(screen.getByText("Enter what it's worth today.")).toBeTruthy()
    fillNameValue('Test Watch', '0')
    click('Continue')
    expect(screen.getByText('Enter a value greater than 0.')).toBeTruthy()
    expect(title()).toBe('Name and value')
  })

  it('refuses a share outside 0–100 on step 3', () => {
    renderWizard({ initialCategory: 'watch' })
    fillNameValue()
    click('Continue')
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    typeMoney('Your share', '150')
    click('Continue')
    expect(screen.getByText('Enter a share from 0 to 100.')).toBeTruthy()
    expect(title()).toBe('Is it all yours?')
  })

  it('refuses a price of 0 and a future purchase date on step 4, saving nothing', () => {
    renderWizard({ initialCategory: 'watch' })
    fillNameValue()
    click('Continue')
    click('Continue')
    typeMoney('Bought for', '0')
    const tomorrow = new Date()
    tomorrow.setDate(tomorrow.getDate() + 1)
    fireEvent.change(screen.getByLabelText('Bought on'), { target: { value: localIso(tomorrow) } })
    click('Add asset')
    expect(screen.getByText('Enter a value greater than 0, or leave it blank.')).toBeTruthy()
    expect(screen.getByText("Pick a date that isn't in the future.")).toBeTruthy()
    expect(screen.getByLabelText('Bought on').getAttribute('max')).toBe(localIso())
    expect(authFetch).not.toHaveBeenCalled()
  })
})

describe('AssetWizard — closing', () => {
  it('closes at once when nothing has been entered', () => {
    const props = renderWizard()
    click('Close')
    expect(props.onClose).toHaveBeenCalled()
    expect(screen.queryByText('Discard this asset?')).toBeNull()
  })

  it('asks before discarding input, and Keep editing keeps it', () => {
    const props = renderWizard({ initialCategory: 'watch' })
    type(/^Name/, 'Test Watch')
    click('Close')
    expect(screen.getByText('Discard this asset?')).toBeTruthy()
    expect(screen.getByText("You'll lose what you've entered.")).toBeTruthy()
    click('Keep editing')
    expect(props.onClose).not.toHaveBeenCalled()
    expect((screen.getByRole('textbox', { name: /^Name/ }) as HTMLInputElement).value).toBe(
      'Test Watch',
    )
    click('Close')
    click('Discard')
    expect(props.onClose).toHaveBeenCalled()
  })

  it('counts a half-typed category as input', () => {
    const props = renderWizard()
    click('New category')
    fireEvent.change(screen.getByPlaceholderText('e.g. Boats, Wine collection'), {
      target: { value: 'Boats' },
    })
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
    expect(screen.getByText('Discard this asset?')).toBeTruthy()
    expect(props.onClose).not.toHaveBeenCalled()
  })
})

describe('AssetWizard — a refused save keeps everything', () => {
  it('409 loan_in_use: back on the loan step, the reason under the loan select, nothing lost', async () => {
    serve({
      '/api/assets': [
        refused(409, {
          detail:
            'Test Mortgage already secures “Test Condo”. A loan can secure one asset — unlink it there first.',
          code: 'conflict',
          reason: 'loan_in_use',
          field: 'loan_account_id',
          holder: { id: 'oa_condo0001', name: 'Test Condo' },
        }),
      ],
    })
    const props = renderWizard({ initialCategory: 'vehicle' })
    fillNameValue('Test Car', '52000')
    click('Continue')
    fireEvent.change(screen.getByRole('combobox', { name: 'Any loan against it?' }), {
      target: { value: MORTGAGE.id },
    })
    click('Continue')
    typeMoney('Bought for', '84000')
    click('Add asset')

    expect((await screen.findByRole('alert')).textContent).toBe(
      'Test Mortgage already secures “Test Condo”. A loan can secure one asset — unlink it there first.',
    )
    expect(title()).toBe('Is it all yours?')
    // The reason sits inside the loan field itself, not somewhere else in the dialog.
    const loanField = screen.getByRole('alert').closest('.ui-field') as HTMLElement
    expect(within(loanField).getByRole('combobox', { name: 'Any loan against it?' })).toBeTruthy()
    expect(
      (screen.getByRole('combobox', { name: 'Any loan against it?' }) as HTMLSelectElement).value,
    ).toBe(MORTGAGE.id)
    expect(props.onCreated).not.toHaveBeenCalled()
    click('Continue')
    expect(digits('Bought for')).toBe('84000')
    click('Back')
    click('Back')
    expect((screen.getByRole('textbox', { name: /^Name/ }) as HTMLInputElement).value).toBe(
      'Test Car',
    )
  })

  it('422 share: back on the ownership step with the reason under Your share', async () => {
    serve({
      '/api/assets': [
        refused(422, {
          detail: 'Your share must be between 0 and 100%.',
          code: 'validation',
          field: 'share_pct',
        }),
      ],
    })
    renderWizard({ initialCategory: 'watch' })
    fillNameValue('Test Watch', '14800')
    click('Continue')
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    typeMoney('Your share', '60')
    click('Continue')
    click('Add asset')
    expect((await screen.findByRole('alert')).textContent).toBe(
      'Your share must be between 0 and 100%.',
    )
    expect(title()).toBe('Is it all yours?')
    expect(digits('Your share')).toBe('60')
  })

  it('a request-shape 422 lands beside the field its loc names', async () => {
    serve({
      '/api/assets': [
        refused(422, {
          detail: [{ loc: ['body', 'value'], msg: 'Input should be a valid number' }],
        }),
      ],
    })
    renderWizard({ initialCategory: 'watch' })
    fillNameValue('Test Watch', '14800')
    click('Continue')
    click('Continue')
    click('Add asset')
    expect((await screen.findByRole('alert')).textContent).toBe(
      'Value: Input should be a valid number',
    )
    expect(title()).toBe('Name and value')
    expect((screen.getByRole('textbox', { name: /^Name/ }) as HTMLInputElement).value).toBe(
      'Test Watch',
    )
  })

  it('a refusal naming no field stays on the last step, said in the dialog', async () => {
    authFetch.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new SyntaxError('Unexpected token <')
      },
    } as unknown as Response)
    renderWizard({ initialCategory: 'watch' })
    fillNameValue()
    click('Continue')
    click('Continue')
    click('Add asset')
    expect((await screen.findByRole('alert')).textContent).toBe(
      "That didn't save (error 500). Try again.",
    )
    expect(title()).toBe('How much did you pay?')
    expect(screen.getByRole('button', { name: 'Add asset' })).toBeTruthy()
  })

  it('creates a staged loan once — a retry after a refused asset links the same loan', async () => {
    const LOAN = { id: 'acc_newloan1', name: 'Test Car Loan', balance: -38000 }
    serve({
      '/api/accounts': [ok(LOAN)],
      '/api/assets': [
        refused(503, { detail: 'The server is busy. Try again.', code: 'unavailable' }),
        ok(CREATED),
      ],
    })
    const props = renderWizard({ initialCategory: 'vehicle', loans: [] })
    fillNameValue('Test Car', '52000')
    click('Continue')
    click('Add a loan')
    type('Loan name', 'Test Car Loan')
    typeMoney('Amount owed', '38000')
    click('Add loan')
    // Staged only — nothing is written before the final save.
    expect(authFetch).not.toHaveBeenCalled()
    click('Continue')
    click('Add asset')

    expect((await screen.findByRole('alert')).textContent).toBe('The server is busy. Try again.')
    expect(posted('/api/accounts')).toEqual([
      { name: 'Test Car Loan', type: 'loan', initial_balance: -38000 },
    ])
    expect(posted('/api/assets')[0]).toMatchObject({ loan_account_id: 'acc_newloan1' })

    click('Add asset')
    await waitFor(() => expect(props.onCreated).toHaveBeenCalledWith(CREATED))
    expect(posted('/api/accounts')).toHaveLength(1)
    expect(posted('/api/assets')[1]).toMatchObject({ loan_account_id: 'acc_newloan1' })
  })

  it('shows the created loan by its real id — no longer "(new)" — after a refused asset', async () => {
    serve({
      '/api/accounts': [ok({ id: 'acc_newloan1', name: 'Test Car Loan', balance: -38000 })],
      '/api/assets': [refused(503, { detail: 'The server is busy. Try again.' })],
    })
    renderWizard({ initialCategory: 'vehicle', loans: [] })
    fillNameValue('Test Car', '52000')
    click('Continue')
    click('Add a loan')
    type('Loan name', 'Test Car Loan')
    typeMoney('Amount owed', '38000')
    click('Add loan')
    click('Continue')
    click('Add asset')
    await screen.findByRole('alert')
    click('Back')
    const select = screen.getByRole('combobox', {
      name: 'Any loan against it?',
    }) as HTMLSelectElement
    expect(select.value).toBe('acc_newloan1')
    expect(select.value).not.toBe(NEW_LOAN)
    expect(select.selectedOptions[0]?.textContent).toBe(`Test Car Loan · ${fmt(38000)}`)
    // Discarding now says the loan stays — it is a real account.
    click('Close')
    expect(
      screen.getByText(
        "You'll lose what you've entered. The loan you added, Test Car Loan, stays in Accounts.",
      ),
    ).toBeTruthy()
  })

  it('a refused staged loan sends you to it with the reason, and writes no asset', async () => {
    serve({
      '/api/accounts': [refused(422, { detail: 'That account name is already taken.' })],
    })
    renderWizard({ initialCategory: 'vehicle', loans: [] })
    fillNameValue('Test Car', '52000')
    click('Continue')
    click('Add a loan')
    type('Loan name', 'Test Car Loan')
    typeMoney('Amount owed', '38000')
    click('Add loan')
    click('Continue')
    click('Add asset')
    expect((await screen.findByRole('alert')).textContent).toBe(
      'That account name is already taken.',
    )
    expect(title()).toBe('Is it all yours?')
    expect(posted('/api/assets')).toEqual([])
  })
})

describe('AssetWizard — one save per press', () => {
  it('a double-click creates one asset and at most one loan; the button says Adding…', async () => {
    let answer: (r: Response) => void = () => {}
    authFetch.mockImplementation(async (path: string) => {
      if (path === '/api/accounts') return ok({ id: 'acc_newloan1', name: 'L', balance: -1 })
      return new Promise<Response>((resolve) => {
        answer = resolve
      })
    })
    const props = renderWizard({ initialCategory: 'vehicle', loans: [] })
    fillNameValue('Test Car', '52000')
    click('Continue')
    click('Add a loan')
    type('Loan name', 'L')
    typeMoney('Amount owed', '1')
    click('Add loan')
    click('Continue')
    const add = screen.getByRole('button', { name: 'Add asset' })
    fireEvent.click(add)
    fireEvent.click(add)
    await waitFor(() => expect(posted('/api/assets')).toHaveLength(1))
    const busy = screen.getByRole('button', { name: 'Adding…' }) as HTMLButtonElement
    expect(busy.disabled).toBe(true)
    fireEvent.click(busy)
    answer(ok(CREATED))
    await waitFor(() => expect(props.onCreated).toHaveBeenCalledTimes(1))
    expect(posted('/api/accounts')).toHaveLength(1)
    expect(posted('/api/assets')).toHaveLength(1)
  })

  it('Enter held down on the last step saves once', async () => {
    serve({ '/api/assets': [ok(CREATED)] })
    const props = renderWizard({ initialCategory: 'watch' })
    fillNameValue()
    click('Continue')
    click('Continue')
    const price = screen.getByRole('textbox', { name: 'Bought for' })
    for (let i = 0; i < 4; i++) fireEvent.keyDown(price, { key: 'Enter' })
    await waitFor(() => expect(props.onCreated).toHaveBeenCalledTimes(1))
    expect(posted('/api/assets')).toHaveLength(1)
  })

  it('never offers a loan to a category that does not take one, even after switching', async () => {
    serve({ '/api/assets': [ok(CREATED)] })
    renderWizard({ initialCategory: 'property' })
    fillNameValue()
    click('Continue')
    fireEvent.change(screen.getByRole('combobox', { name: 'Any loan against it?' }), {
      target: { value: MORTGAGE.id },
    })
    click('Back')
    click('Back')
    click('Watch & jewellery')
    click('Continue')
    expect(screen.queryByRole('combobox', { name: 'Any loan against it?' })).toBeNull()
    click('Continue')
    click('Add asset')
    await waitFor(() => expect(posted('/api/assets')).toHaveLength(1))
    expect(posted('/api/assets')[0]).not.toHaveProperty('loan_account_id')
    expect(within(document.body).queryByText(/secures/)).toBeNull()
  })
})

describe('AssetWizard — review regressions', () => {
  it('a held Enter never skips past a step — only a fresh press advances', () => {
    renderWizard({ initialCategory: 'watch' })
    fillNameValue()
    const name = screen.getByRole('textbox', { name: /^Name/ })
    fireEvent.keyDown(name, { key: 'Enter', repeat: true })
    expect(title()).toBe('Name and value')
    fireEvent.keyDown(name, { key: 'Enter' })
    expect(title()).toBe('Is it all yours?')
  })

  it('a held Enter on step 3 never skips the optional step 4', async () => {
    const props = renderWizard({ initialCategory: 'watch' })
    fillNameValue()
    click('Continue')
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    const withWhom = screen.getByRole('textbox', { name: 'Shared with (optional)' })
    fireEvent.keyDown(withWhom, { key: 'Enter' })
    expect(title()).toBe('How much did you pay?')
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Bought for' }), {
      key: 'Enter',
      repeat: true,
    })
    // A save would reach the network only after an await — let it, if it started.
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(authFetch).not.toHaveBeenCalled()
    expect(props.onCreated).not.toHaveBeenCalled()
  })

  it('Escape inside the inline loan form closes only that form, inside the real dialog', () => {
    const props = renderWizard({ initialCategory: 'vehicle', loans: [] })
    fillNameValue('Test Car', '52000')
    click('Continue')
    click('Add a loan')
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Loan name' }), { key: 'Escape' })
    expect(screen.queryByRole('textbox', { name: 'Loan name' })).toBeNull()
    expect(title()).toBe('Is it all yours?')
    expect(screen.queryByText('Discard this asset?')).toBeNull()
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it('handing off to Lucius with input asks to discard it first', () => {
    const props = renderWizard()
    click('Watch & jewellery')
    type(/^Name/, 'Test Watch')
    click('Back')
    click('Describe it to Lucius')
    expect(screen.getByText('Discard this asset?')).toBeTruthy()
    expect(props.onAskLucius).not.toHaveBeenCalled()
    click('Discard')
    expect(props.onClose).toHaveBeenCalled()
    expect(props.onAskLucius).toHaveBeenCalledWith('Add an asset: ')
  })

  it('hands off to Lucius at once when nothing was typed', () => {
    const props = renderWizard()
    click('Describe it to Lucius')
    expect(screen.queryByText('Discard this asset?')).toBeNull()
    expect(props.onClose).toHaveBeenCalled()
    expect(props.onAskLucius).toHaveBeenCalledWith('Add an asset: ')
  })
})

// A browser's autofill, or a script's fill(), fires several fields' events in one task: the
// MoneyField's blur commit and the next field's change land before React renders in between.
// Each must merge into the newest value, never rebuild it from the step's last-rendered copy.
describe('AssetWizard — same-tick field updates', () => {
  /** A MoneyField holding an uncommitted draft — it commits on the blur that comes next. */
  function draftMoney(label: string | RegExp, text: string): HTMLElement {
    const input = screen.getByRole('textbox', { name: label })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: text } })
    return input
  }

  it('keeps a price committed in the same tick as the purchase date', async () => {
    serve({ '/api/assets': [ok(CREATED)] })
    const props = renderWizard({ initialCategory: 'watch' })
    fillNameValue()
    click('Continue')
    click('Continue')
    const price = draftMoney('Bought for', '9000')
    act(() => {
      fireEvent.blur(price)
      fireEvent.change(screen.getByLabelText('Bought on'), { target: { value: '2019-06-15' } })
    })
    expect(digits('Bought for')).toBe('9000')
    click('Add asset')
    await waitFor(() => expect(props.onCreated).toHaveBeenCalled())
    expect(posted('/api/assets')[0]).toMatchObject({ cost: 9000, cost_date: '2019-06-15' })
  })

  it('keeps a share committed in the same tick as who it is shared with', async () => {
    serve({ '/api/assets': [ok(CREATED)] })
    const props = renderWizard({ initialCategory: 'watch' })
    fillNameValue()
    click('Continue')
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    const share = draftMoney('Your share', '60')
    act(() => {
      fireEvent.blur(share)
      fireEvent.change(screen.getByRole('textbox', { name: 'Shared with (optional)' }), {
        target: { value: 'Joint with A. Lim' },
      })
    })
    click('Continue')
    click('Add asset')
    await waitFor(() => expect(props.onCreated).toHaveBeenCalled())
    expect(posted('/api/assets')[0]).toMatchObject({
      share_pct: 60,
      share_note: 'Joint with A. Lim',
    })
  })
})

describe('AssetWizard — an error leaves once its field is fixed', () => {
  const invalid = (label: string | RegExp) =>
    screen.getByRole('textbox', { name: label }).classList.contains('ui-input--invalid')

  it('step 2: fixing the name clears only the name error, and the field is marked meanwhile', () => {
    renderWizard({ initialCategory: 'watch' })
    click('Continue')
    expect(screen.getByText('Enter a name.')).toBeTruthy()
    expect(screen.getByText("Enter what it's worth today.")).toBeTruthy()
    expect(invalid(/^Name/)).toBe(true)
    expect(invalid(/Worth today/)).toBe(true)
    type(/^Name/, 'Test Watch')
    expect(screen.queryByText('Enter a name.')).toBeNull()
    expect(invalid(/^Name/)).toBe(false)
    expect(screen.getByText("Enter what it's worth today.")).toBeTruthy()
    typeMoney(/Worth today/, '14800')
    expect(screen.queryByText("Enter what it's worth today.")).toBeNull()
  })

  it('step 4: moving a future date back clears its error', () => {
    renderWizard({ initialCategory: 'watch' })
    fillNameValue()
    click('Continue')
    click('Continue')
    fireEvent.change(screen.getByLabelText('Bought on'), { target: { value: '2999-01-01' } })
    click('Add asset')
    expect(screen.getByText("Pick a date that isn't in the future.")).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Bought on'), { target: { value: '2019-01-01' } })
    expect(screen.queryByText("Pick a date that isn't in the future.")).toBeNull()
  })

  it("the new loan's own errors leave as its fields are fixed — the server's too", async () => {
    serve({
      '/api/accounts': [
        refused(422, { detail: 'That account name is already taken.', field: 'name' }),
      ],
    })
    renderWizard({ initialCategory: 'vehicle', loans: [] })
    fillNameValue('Test Car', '52000')
    click('Continue')
    click('Add a loan')
    click('Add loan')
    expect(screen.getByText('Enter a name for this loan.')).toBeTruthy()
    type('Loan name', 'Test Car Loan')
    expect(screen.queryByText('Enter a name for this loan.')).toBeNull()
    typeMoney('Amount owed', '38000')
    click('Add loan')
    click('Continue')
    click('Add asset')
    expect((await screen.findByRole('alert')).textContent).toBe(
      'That account name is already taken.',
    )
    type('Loan name', 'Test Car Loan 2')
    expect(screen.queryByText('That account name is already taken.')).toBeNull()
  })
})

describe('AssetWizard — an amount it cannot read is never sent', () => {
  it('holds step 2 on an unreadable value — by Continue or by Enter', () => {
    renderWizard({ initialCategory: 'watch' })
    type(/^Name/, 'Test Watch')
    // A good figure first: the bug kept it and moved on when the new text was unreadable.
    typeMoney(/Worth today/, '14800')
    typeMoney(/Worth today/, 'fourteen grand')
    click('Continue')
    expect(title()).toBe('Name and value')
    expect(screen.getByText('Enter an amount of 0 or more.')).toBeTruthy()
    fireEvent.keyDown(screen.getByRole('textbox', { name: /^Name/ }), { key: 'Enter' })
    expect(title()).toBe('Name and value')
  })

  it('reads "S$820,000" as 820000 on the way to the asset', async () => {
    serve({ '/api/assets': [ok(CREATED)] })
    const props = renderWizard({ initialCategory: 'watch' })
    type(/^Name/, 'Test Watch')
    typeMoney(/Worth today/, 'S$820,000')
    click('Continue')
    click('Continue')
    click('Add asset')
    await waitFor(() => expect(props.onCreated).toHaveBeenCalled())
    expect(posted('/api/assets')[0]).toMatchObject({ value: 820000 })
  })

  it('never stages a loan whose amount it cannot read', () => {
    renderWizard({ initialCategory: 'vehicle', loans: [] })
    fillNameValue('Test Car', '52000')
    click('Continue')
    click('Add a loan')
    type('Loan name', 'Test Car Loan')
    typeMoney('Amount owed', '38000')
    typeMoney('Amount owed', 'thirty-eight grand')
    click('Add loan')
    expect(screen.getByRole('textbox', { name: 'Loan name' })).toBeTruthy()
    expect(screen.queryByRole('combobox', { name: 'Any loan against it?' })).toBeNull()
  })
})

describe("AssetWizard — text stops at the server's caps", () => {
  it('a new category at 60 characters', () => {
    renderWizard()
    click('New category')
    const label = screen.getByRole('textbox', { name: 'New category name' }) as HTMLInputElement
    expect(label.maxLength).toBe(60)
  })

  it('a name at 120 characters', () => {
    renderWizard({ initialCategory: 'watch' })
    expect((screen.getByRole('textbox', { name: /^Name/ }) as HTMLInputElement).maxLength).toBe(120)
  })
})

// Review R13: an open "Add a loan" form holding anything not yet added — or an amount it can't
// read — holds the step. Its edits are never applied silently: it is added or cancelled first.
describe("AssetWizard — an open loan form holds the step until it's added or cancelled", () => {
  const HOLD = 'Add or cancel this loan first.'
  const TEST_LOAN = { id: 'acc_newloan1', name: 'Test Car Loan', balance: -38000 }
  const loanForm = () => screen.queryByRole('group', { name: 'New loan account' })

  /** A vehicle's step 3 with a loan staged at 38,000, its form open again ("Edit new loan"). */
  function reopenStagedLoan() {
    const props = renderWizard({ initialCategory: 'vehicle', loans: [] })
    fillNameValue('Test Car', '52000')
    click('Continue')
    click('Add a loan')
    type('Loan name', 'Test Car Loan')
    typeMoney('Amount owed', '38000')
    click('Add loan')
    click('Edit new loan')
    return props
  }

  function expectHeld() {
    expect(title()).toBe('Is it all yours?')
    const said = screen.getByText(HOLD)
    expect(said.getAttribute('role')).toBe('alert')
    expect(loanForm()?.contains(said)).toBe(true)
    // Keyboard focus is taken to the form that holds the step.
    expect(document.activeElement).toBe(loanForm())
  }

  it("holds Continue over an amount it can't read — never the staged 38,000", () => {
    reopenStagedLoan()
    typeMoney('Amount owed', '4O,000')
    click('Continue')
    expectHeld()
    expect(authFetch).not.toHaveBeenCalled()
  })

  it('holds Enter on step 3 the same way', () => {
    reopenStagedLoan()
    typeMoney('Amount owed', '4O,000')
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Shared with (optional)' }), {
      key: 'Enter',
    })
    expectHeld()
  })

  it('holds Continue over a figure typed but not added; once added, that figure is the loan', async () => {
    serve({
      '/api/accounts': [ok({ ...TEST_LOAN, balance: -40000 })],
      '/api/assets': [ok(CREATED)],
    })
    const props = reopenStagedLoan()
    typeMoney('Amount owed', '40000')
    click('Continue')
    expectHeld()
    click('Add loan')
    expect(screen.queryByText(HOLD)).toBeNull()
    click('Continue')
    click('Add asset')
    await waitFor(() => expect(props.onCreated).toHaveBeenCalled())
    expect(posted('/api/accounts')).toEqual([
      { name: 'Test Car Loan', type: 'loan', initial_balance: -40000 },
    ])
  })

  it('holds Continue over a new loan typed but never added — cancelled, nothing is staged', async () => {
    serve({ '/api/assets': [ok(CREATED)] })
    const props = renderWizard({ initialCategory: 'vehicle', loans: [] })
    fillNameValue('Test Car', '52000')
    click('Continue')
    click('Add a loan')
    type('Loan name', 'Test Car Loan')
    click('Continue')
    expectHeld()
    click('Cancel')
    click('Continue')
    click('Add asset')
    await waitFor(() => expect(props.onCreated).toHaveBeenCalled())
    expect(posted('/api/accounts')).toEqual([])
    expect(posted('/api/assets')[0]).not.toHaveProperty('loan_account_id')
  })

  it('lets an untouched open form go — it closes as if cancelled, the staged loan kept', async () => {
    serve({ '/api/accounts': [ok(TEST_LOAN)], '/api/assets': [ok(CREATED)] })
    const props = reopenStagedLoan()
    // Another field holds the step: the untouched form still closes, as Cancel would.
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    typeMoney('Your share', '150')
    click('Continue')
    expect(title()).toBe('Is it all yours?')
    expect(screen.queryByText('New loan account')).toBeNull()
    expect(screen.queryByText(HOLD)).toBeNull()
    typeMoney('Your share', '50')
    click('Continue')
    click('Add asset')
    await waitFor(() => expect(props.onCreated).toHaveBeenCalled())
    expect(posted('/api/accounts')).toEqual([
      { name: 'Test Car Loan', type: 'loan', initial_balance: -38000 },
    ])
  })
})
