// @vitest-environment jsdom
//
// The create wizard's other three steps, each rendered alone (the wizard's flow is covered by
// AssetWizard.test.tsx). Synthetic fixtures only.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { AssetCategory } from '../../../types/assets'
import { setBaseCurrency } from '../../../utils/format'
import { AssetsApiError } from './api'
import { CategoryStep, DescribeToLucius } from './CategoryStep'
import { NameValueStep } from './NameValueStep'
import { PurchaseStep } from './PurchaseStep'

setBaseCurrency('SGD')
afterEach(cleanup)

const CATS: AssetCategory[] = [
  {
    key: 'property',
    label: 'Property',
    plural: 'Property',
    color: 'var(--cat-property)',
    custom: false,
  },
  {
    key: 'watch',
    label: 'Watch & jewellery',
    plural: 'Watches & jewellery',
    color: 'var(--cat-watch)',
    custom: false,
  },
]

describe('CategoryStep', () => {
  function renderStep(overrides: Partial<Parameters<typeof CategoryStep>[0]> = {}) {
    const props = {
      cats: CATS,
      newCatLabel: '',
      onNewCatLabel: vi.fn(),
      onPick: vi.fn(),
      onCreateCategory: vi.fn(),
      ...overrides,
    }
    render(<CategoryStep {...props} />)
    return props
  }

  it('picks a category with one tap', () => {
    const props = renderStep()
    fireEvent.click(screen.getByRole('button', { name: 'Watch & jewellery' }))
    expect(props.onPick).toHaveBeenCalledWith('watch')
  })

  it('creates a category from its pinned row, then picks it', async () => {
    const created = {
      key: 'c_boats',
      label: 'Boats',
      plural: 'Boats',
      color: 'var(--cat-other)',
      custom: true,
    }
    const onCreateCategory = vi.fn().mockResolvedValue(created)
    const props = renderStep({ newCatLabel: 'Boats', onCreateCategory })
    // A label typed earlier reopens the row with it.
    expect(
      (screen.getByPlaceholderText('e.g. Boats, Wine collection') as HTMLInputElement).value,
    ).toBe('Boats')
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    await waitFor(() => expect(props.onPick).toHaveBeenCalledWith('c_boats'))
    expect(onCreateCategory).toHaveBeenCalledWith('Boats')
    expect(props.onNewCatLabel).toHaveBeenLastCalledWith('')
  })

  it("shows a refused category's reason under the row", async () => {
    const onCreateCategory = vi
      .fn()
      .mockRejectedValue(
        new AssetsApiError('That category already exists.', 409, 'conflict', null, null, null),
      )
    const props = renderStep({ newCatLabel: 'Property', onCreateCategory })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    expect((await screen.findByRole('alert')).textContent).toBe('That category already exists.')
    expect(props.onPick).not.toHaveBeenCalled()
  })

  it('ends the grid with a New category tile that opens and closes its name field', () => {
    const props = renderStep({ newCatLabel: '' })
    const tiles = Array.from(document.querySelectorAll('.as-pick button'))
    const tile = screen.getByRole('button', { name: 'New category' })
    expect(tiles[tiles.length - 1]).toBe(tile)
    expect(tile.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(tile)
    expect(tile.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('textbox', { name: 'New category name' })).toBeTruthy()
    fireEvent.click(tile)
    expect(screen.queryByRole('textbox', { name: 'New category name' })).toBeNull()
    expect(props.onNewCatLabel).toHaveBeenLastCalledWith('')
  })

  it('opens the row from its trigger and reports what is typed', () => {
    const props = renderStep()
    fireEvent.click(screen.getByRole('button', { name: 'New category' }))
    fireEvent.change(screen.getByPlaceholderText('e.g. Boats, Wine collection'), {
      target: { value: 'Wine' },
    })
    expect(props.onNewCatLabel).toHaveBeenLastCalledWith('Wine')
  })

  it('offers Lucius as a card of its own, with an example of what to say', () => {
    // Owner 2026-09-28: "make describe with lucius more standing out, and the description
    // there is pretty weird" — a Lucius-tinted card, and an example instead of an abstraction.
    const onAsk = vi.fn()
    render(<DescribeToLucius onAsk={onAsk} />)
    const cta = screen.getByRole('button', { name: 'Describe it to Lucius' })
    expect(cta.classList.contains('as-lucius-cta')).toBe(true)
    expect(cta.getAttribute('aria-describedby')).toBeTruthy()
    expect(
      screen.getByText(
        'Say it in your own words, like “my 2019 Rolex, bought for S$9,000”. Lucius adds it after you confirm.',
      ),
    ).toBeTruthy()
    fireEvent.click(cta)
    expect(onAsk).toHaveBeenCalled()
  })
})

describe('NameValueStep', () => {
  function renderStep(overrides: Partial<Parameters<typeof NameValueStep>[0]> = {}) {
    const props = {
      category: 'property',
      name: '',
      value: null,
      currency: 'SGD',
      errors: {},
      onName: vi.fn(),
      onValue: vi.fn(),
      ...overrides,
    }
    render(<NameValueStep {...props} />)
    return props
  }

  it('asks for a name (focused, with a category example) and what it is worth today', () => {
    renderStep()
    const name = screen.getByRole('textbox', { name: /Name/ })
    expect(document.activeElement).toBe(name)
    expect(name.getAttribute('placeholder')).toBe('e.g. 88 Marina Rise')
    expect(screen.getByRole('textbox', { name: /Worth today/ })).toBeTruthy()
    expect(screen.getByText('Your best estimate is fine')).toBeTruthy()
    // The currency symbol sits inside the field, not in a second box beside it.
    expect(document.querySelector('.ui-money-field__prefix')?.textContent).toBe('S$')
  })

  it('asks a policy for its cash value, and what that means', () => {
    renderStep({ category: 'policy' })
    expect(screen.getByRole('textbox', { name: /Cash value today/ })).toBeTruthy()
    expect(
      screen.getByText('The surrender value on your latest statement — not the sum assured.'),
    ).toBeTruthy()
  })

  it('reports a typed name and value, and a cleared value as null', () => {
    const props = renderStep({ value: 14800 })
    fireEvent.change(screen.getByRole('textbox', { name: /Name/ }), {
      target: { value: 'Rolex' },
    })
    expect(props.onName).toHaveBeenCalledWith('Rolex')
    const value = screen.getByRole('textbox', { name: /Worth today/ })
    fireEvent.focus(value)
    fireEvent.change(value, { target: { value: '' } })
    fireEvent.blur(value)
    expect(props.onValue).toHaveBeenCalledWith(null)
  })

  it('shows each error beside its field', () => {
    renderStep({ errors: { name: 'Enter a name.', value: "Enter what it's worth today." } })
    const alerts = screen.getAllByRole('alert').map((a) => a.textContent)
    expect(alerts).toEqual(['Enter a name.', "Enter what it's worth today."])
  })

  it("leads with the category's own guidance, as a plain intro line", () => {
    renderStep({ category: 'metal', hint: 'Gold ETFs like GLD live under Investments.' })
    const intro = screen.getByText('Gold ETFs like GLD live under Investments.')
    expect(intro.tagName).toBe('P')
    expect(intro.closest('.as-hint')).toBeNull()
    const name = screen.getByRole('textbox', { name: /^Name/ })
    expect(intro.compareDocumentPosition(name) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

describe('PurchaseStep', () => {
  function renderStep(overrides: Partial<Parameters<typeof PurchaseStep>[0]> = {}) {
    const props = {
      category: 'watch',
      value: { cost: null, costDate: '' },
      onChange: vi.fn(),
      currency: 'SGD',
      maxDate: '2026-09-26',
      errors: {},
      ...overrides,
    }
    render(<PurchaseStep {...props} />)
    return props
  }

  it('says it is optional and what it unlocks; price focused, date capped at today', () => {
    renderStep()
    expect(screen.getByText('Optional — adds your gain and yearly return.')).toBeTruthy()
    const price = screen.getByRole('textbox', { name: 'Bought for' })
    expect(document.activeElement).toBe(price)
    expect(screen.getByLabelText('Bought on').getAttribute('max')).toBe('2026-09-26')
  })

  it('asks a policy for premiums paid and its start date instead', () => {
    renderStep({ category: 'policy' })
    expect(
      screen.getByText("Optional. Shows whether the cash value is above what you've paid."),
    ).toBeTruthy()
    expect(screen.getByRole('textbox', { name: 'Premiums paid so far' })).toBeTruthy()
    expect(screen.getByLabelText('Policy started on')).toBeTruthy()
    expect(screen.queryByText(/Bought/)).toBeNull()
  })

  it('reports each change as a patch of that field alone — never a rebuilt copy', () => {
    const props = renderStep()
    const price = screen.getByRole('textbox', { name: 'Bought for' })
    fireEvent.focus(price)
    fireEvent.change(price, { target: { value: '9000' } })
    fireEvent.blur(price)
    expect(props.onChange).toHaveBeenLastCalledWith({ cost: 9000 })
    fireEvent.change(screen.getByLabelText('Bought on'), { target: { value: '2019-08-01' } })
    expect(props.onChange).toHaveBeenLastCalledWith({ costDate: '2019-08-01' })
  })

  it('shows each error beside its field', () => {
    renderStep({
      errors: {
        cost: 'Enter a value greater than 0, or leave it blank.',
        cost_date: "Pick a date that isn't in the future.",
      },
    })
    expect(screen.getAllByRole('alert').map((a) => a.textContent)).toEqual([
      'Enter a value greater than 0, or leave it blank.',
      "Pick a date that isn't in the future.",
    ])
  })
})
