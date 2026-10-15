// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setDisplayFormats } from '../../utils/format'
import { parseAmount } from './amountText'
import { MoneyField } from './MoneyField'

afterEach(cleanup)

describe('MoneyField', () => {
  it('looks editable, accepts an amount above any suggested range, and has no maximum', () => {
    const onValueChange = vi.fn()
    render(
      <MoneyField
        aria-label="Monthly retirement spending"
        value={5_000}
        formattedValue="S$5,000.00"
        suffix="/month"
        onValueChange={onValueChange}
      />,
    )

    const input = screen.getByRole('textbox', { name: 'Monthly retirement spending' })
    expect(input.classList.contains('ui-input')).toBe(true)
    expect(input.getAttribute('max')).toBeNull()
    expect(input.getAttribute('value')).toBe('S$5,000.00')
    expect(screen.getByText('/month')).toBeTruthy()

    fireEvent.focus(input)
    expect(input.getAttribute('value')).toBe('5000')
    fireEvent.change(input, { target: { value: '500000000' } })
    fireEvent.blur(input)
    expect(onValueChange).toHaveBeenCalledWith(500_000_000)
  })

  it('keeps the last valid value and reports a negative or blank amount', () => {
    const onValueChange = vi.fn()
    render(
      <MoneyField
        aria-label="Monthly saving"
        value={1_000}
        formattedValue="S$1,000.00"
        suffix="/month"
        onValueChange={onValueChange}
      />,
    )

    const input = screen.getByRole('textbox', { name: 'Monthly saving' })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.blur(input)

    expect(onValueChange).not.toHaveBeenCalled()
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByRole('alert').textContent).toBe('Enter an amount of 0 or more.')
  })

  it('rejects an ambiguous comma decimal instead of silently multiplying it', () => {
    const onValueChange = vi.fn()
    render(
      <MoneyField
        aria-label="Monthly saving"
        value={1_000}
        formattedValue="1.000,00 €"
        onValueChange={onValueChange}
      />,
    )

    const input = screen.getByRole('textbox', { name: 'Monthly saving' })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '3500,50' } })
    fireEvent.blur(input)

    expect(onValueChange).not.toHaveBeenCalled()
    expect(input.getAttribute('aria-invalid')).toBe('true')
  })

  it('accepts an amount typed the way it displays, grouped in thousands', () => {
    const onValueChange = vi.fn()
    render(
      <MoneyField
        aria-label="Worth today"
        value={0}
        formattedValue="0"
        onValueChange={onValueChange}
      />,
    )
    const input = screen.getByRole('textbox', { name: 'Worth today' })
    for (const typed of ['850,000', '1,234,567.5', '850 000', '850\u202f000']) {
      fireEvent.focus(input)
      fireEvent.change(input, { target: { value: typed } })
      fireEvent.blur(input)
    }
    expect(onValueChange.mock.calls.map(([v]) => v)).toEqual([
      850_000, 1_234_567.5, 850_000, 850_000,
    ])
    expect(input.getAttribute('aria-invalid')).toBeNull()

    for (const ambiguous of ['85,00', '8,50,000', '850,000 000']) {
      fireEvent.focus(input)
      fireEvent.change(input, { target: { value: ambiguous } })
      fireEvent.blur(input)
      expect(input.getAttribute('aria-invalid')).toBe('true')
    }
    expect(onValueChange).toHaveBeenCalledTimes(4)
  })

  it("marks a caller's invalid field without adding its own message, and keeps aria-invalid", () => {
    render(
      <MoneyField
        aria-label="Worth today"
        aria-invalid
        invalid
        value={0}
        formattedValue="0"
        onValueChange={vi.fn()}
      />,
    )
    const input = screen.getByRole('textbox', { name: 'Worth today' })
    expect(input.classList.contains('ui-input--invalid')).toBe(true)
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it("keeps a FormField's aria-invalid on a field it finds no fault with", () => {
    render(
      <MoneyField
        aria-label="Worth today"
        aria-invalid
        value={0}
        formattedValue="0"
        onValueChange={vi.fn()}
      />,
    )
    expect(screen.getByRole('textbox', { name: 'Worth today' }).getAttribute('aria-invalid')).toBe(
      'true',
    )
  })

  it('opens an autofocused, prefilled field on its formatted figure until you type', () => {
    const onValueChange = vi.fn()
    render(
      <MoneyField
        aria-label="Value"
        autoFocus
        value={850_000}
        formattedValue="850,000"
        onValueChange={onValueChange}
      />,
    )
    const input = screen.getByRole('textbox', { name: 'Value' })
    expect(document.activeElement).toBe(input)
    expect(input.getAttribute('value')).toBe('850,000')
    fireEvent.change(input, { target: { value: '9' } })
    expect(input.getAttribute('value')).toBe('9')
    fireEvent.blur(input)
    expect(onValueChange).toHaveBeenCalledWith(9)
    // A later focus edits raw digits, as ever.
    fireEvent.focus(input)
    expect(input.getAttribute('value')).toBe('850000')
  })

  it('cancels on Escape and emits only once when Enter commits', () => {
    const onValueChange = vi.fn()
    render(
      <MoneyField
        aria-label="Monthly saving"
        value={1_000}
        formattedValue="S$1,000.00"
        suffix="/month"
        onValueChange={onValueChange}
      />,
    )

    const input = screen.getByRole('textbox', { name: 'Monthly saving' })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '2500' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    fireEvent.blur(input)
    expect(onValueChange).not.toHaveBeenCalled()

    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '3000' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.blur(input)
    expect(onValueChange).toHaveBeenCalledTimes(1)
    expect(onValueChange).toHaveBeenCalledWith(3_000)
  })

  it('draws a prefix inside the field and keeps it while editing', () => {
    render(
      <MoneyField
        aria-label="Worth today"
        value={14_800}
        formattedValue="14,800"
        prefix="S$"
        onValueChange={vi.fn()}
      />,
    )
    const input = screen.getByRole('textbox', { name: 'Worth today' })
    const prefix = document.querySelector('.ui-money-field__prefix')
    expect(prefix?.textContent).toBe('S$')
    expect(prefix?.getAttribute('aria-hidden')).toBe('true')
    // Not part of the editable text — the symbol never has to be typed or deleted.
    expect(input.getAttribute('value')).toBe('14,800')
    fireEvent.focus(input)
    expect(input.getAttribute('value')).toBe('14800')
    expect(document.querySelector('.ui-money-field__prefix')?.textContent).toBe('S$')
  })

  it('renders no prefix unless asked', () => {
    render(<MoneyField aria-label="Saving" value={1} formattedValue="1" onValueChange={vi.fn()} />)
    expect(document.querySelector('.ui-money-field__prefix')).toBeNull()
    expect(screen.getByRole('textbox').getAttribute('prefix')).toBeNull()
  })
})

describe('MoneyField — text it cannot read is never a number', () => {
  function field(onInvalidChange = vi.fn(), onValueChange = vi.fn()) {
    render(
      <MoneyField
        aria-label="Sold for"
        value={850_000}
        formattedValue="850,000"
        onValueChange={onValueChange}
        onInvalidChange={onInvalidChange}
      />,
    )
    return {
      input: screen.getByRole('textbox', { name: 'Sold for' }),
      onInvalidChange,
      onValueChange,
    }
  }
  function commit(input: HTMLElement, text: string) {
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: text } })
    fireEvent.blur(input)
  }

  it('tells its owner, keeps showing what was typed, and never commits the old number', () => {
    const { input, onInvalidChange, onValueChange } = field()
    commit(input, 'about five')
    expect(onInvalidChange).toHaveBeenLastCalledWith(true)
    expect(onValueChange).not.toHaveBeenCalled()
    expect(input.getAttribute('value')).toBe('about five')
    expect(screen.getByRole('alert')).toBeTruthy()
    // Refocusing edits what was typed — it is not swapped for the old figure.
    fireEvent.focus(input)
    expect(input.getAttribute('value')).toBe('about five')
    fireEvent.change(input, { target: { value: '900000' } })
    fireEvent.blur(input)
    expect(onInvalidChange).toHaveBeenLastCalledWith(false)
    expect(onValueChange).toHaveBeenLastCalledWith(900_000)
  })

  it('draws its message under the box the prefix and suffix centre on, never inside it', () => {
    render(
      <MoneyField
        aria-label="Sold for"
        prefix="S$"
        suffix="/month"
        value={850_000}
        formattedValue="850,000"
        onValueChange={vi.fn()}
      />,
    )
    const input = screen.getByRole('textbox', { name: 'Sold for' })
    commit(input, 'about five')
    const box = input.parentElement as HTMLElement
    expect(box.classList.contains('ui-money-field__box')).toBe(true)
    expect(box.querySelector('.ui-money-field__prefix')).toBeTruthy()
    expect(box.querySelector('.ui-money-field__suffix')).toBeTruthy()
    expect(box.contains(screen.getByRole('alert'))).toBe(false)
    // The prefix and suffix centre on `top: 50%` of their positioned ancestor. On the whole
    // field, the message line under the input pulled "S$" down onto the border.
    const css = readFileSync(resolve(__dirname, '../../index.css'), 'utf8')
    const rule = (selector: string) =>
      css.match(new RegExp(`\\n\\s*${selector.replace(/\./g, '\\.')} \\{([^}]*)\\}`))?.[1] ?? ''
    expect(rule('.ui-money-field__box')).toMatch(/position:\s*relative/)
    expect(rule('.ui-money-field')).not.toMatch(/position:\s*relative/)
  })

  it('commits what is typed after an Enter or Escape — the next blur is never skipped', () => {
    const { input, onInvalidChange, onValueChange } = field()
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '900000' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onValueChange).toHaveBeenLastCalledWith(900_000)
    fireEvent.change(input, { target: { value: '950000' } })
    fireEvent.blur(input)
    expect(onValueChange).toHaveBeenLastCalledWith(950_000)
    // Unreadable text typed after an Enter is reported, never left behind the Enter's figure.
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '960000' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.change(input, { target: { value: 'nine fifty k' } })
    fireEvent.blur(input)
    expect(onInvalidChange).toHaveBeenLastCalledWith(true)
    expect(screen.getByRole('alert')).toBeTruthy()
    // Escape, then a new figure: the blur commits it.
    fireEvent.focus(input)
    fireEvent.keyDown(input, { key: 'Escape' })
    fireEvent.change(input, { target: { value: '970000' } })
    fireEvent.blur(input)
    expect(onValueChange).toHaveBeenLastCalledWith(970_000)
    expect(onInvalidChange).toHaveBeenLastCalledWith(false)
  })

  it("reads only the field's own currency around the figure — any other currency is refused", () => {
    const { input, onInvalidChange, onValueChange } = field()
    for (const text of ['820,000 SGD', 'S$ 820,000', '820,000 S$']) commit(input, text)
    expect(onValueChange.mock.calls.map(([v]) => v)).toEqual([820_000, 820_000, 820_000])
    // Yen, euros, dong, won, another dollar, three stray letters: never an S$ figure.
    const foreign = ['¥2,000,000', 'JPY 2,000,000', 'EUR 14,800', '€14,800', '₫20,000,000']
    for (const text of [...foreign, '₩1,000,000', 'US$ 900,000', 'HK$900,000', 'abc 5']) {
      commit(input, text)
      expect(onInvalidChange).toHaveBeenLastCalledWith(true)
    }
    expect(onValueChange).toHaveBeenCalledTimes(3)
  })

  it('a field in another currency reads its own symbol and nothing else', () => {
    const onValueChange = vi.fn()
    render(
      <MoneyField
        aria-label="Worth"
        prefix="€"
        value={1}
        formattedValue="1"
        onValueChange={onValueChange}
      />,
    )
    const input = screen.getByRole('textbox', { name: 'Worth' })
    for (const text of ['€14,800', 'S$14,800', 'SGD 14,800', '$14,800']) commit(input, text)
    expect(onValueChange.mock.calls.map(([v]) => v)).toEqual([14_800])
  })

  it('shows a figure set from outside instead of the unreadable text it replaced', () => {
    const onInvalidChange = vi.fn()
    const props = { 'aria-label': 'Monthly saving', onValueChange: vi.fn(), onInvalidChange }
    const { rerender } = render(<MoneyField {...props} value={1_000} formattedValue="1,000" />)
    const input = screen.getByRole('textbox', { name: 'Monthly saving' })
    commit(input, 'lots')
    expect(onInvalidChange).toHaveBeenLastCalledWith(true)
    // ↺ Reset, or a loaded scenario: the plan now runs on 2,500 — the field must say so.
    rerender(<MoneyField {...props} value={2_500} formattedValue="2,500" />)
    expect(input.getAttribute('value')).toBe('2,500')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(onInvalidChange).toHaveBeenLastCalledWith(false)
  })

  it('Escape puts the last good figure back and clears the report', () => {
    const { input, onInvalidChange } = field()
    commit(input, 'about five')
    fireEvent.focus(input)
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(onInvalidChange).toHaveBeenLastCalledWith(false)
    expect(input.getAttribute('value')).toBe('850,000')
  })

  it('clears its report when it goes away', () => {
    const onInvalidChange = vi.fn()
    const { unmount } = render(
      <MoneyField
        aria-label="Sold for"
        value={1}
        formattedValue="1"
        onValueChange={vi.fn()}
        onInvalidChange={onInvalidChange}
      />,
    )
    commit(screen.getByRole('textbox', { name: 'Sold for' }), 'nope')
    unmount()
    expect(onInvalidChange).toHaveBeenLastCalledWith(false)
  })

  it('reads the obvious forms: a currency typed in front, and k / m shorthand', () => {
    const { input, onValueChange } = field()
    const read: [string, number][] = [
      ['S$820,000', 820_000],
      ['$1,234.50', 1_234.5],
      ['SGD 820,000', 820_000],
      ['sgd820000', 820_000],
      ['860k', 860_000],
      ['2.5K', 2_500],
      ['1.2m', 1_200_000],
      ['S$ 1.5M', 1_500_000],
    ]
    for (const [text] of read) commit(input, text)
    expect(onValueChange.mock.calls.map(([v]) => v)).toEqual(read.map(([, v]) => v))
  })
})

describe("MoneyField — reads amounts in the user's own number format", () => {
  afterEach(() => setDisplayFormats({}))

  const readings = (texts: string[]) => texts.map((text) => parseAmount(text))

  it('dot format (1.234.567,89): the dot groups thousands and the comma is the decimal', () => {
    setDisplayFormats({ number_format: 'dot' })
    expect(
      readings(['860.000', '1.234.567,5', '860000,50', '1,2m', 'S$ 820.000', '860k', '0,5']),
    ).toEqual([860_000, 1_234_567.5, 860_000.5, 1_200_000, 820_000, 860_000, 0.5])
  })

  it('space format (1 234 567,89): any space groups, the comma is the decimal', () => {
    setDisplayFormats({ number_format: 'space' })
    expect(readings(['860 000', '860\u202f000', '860\u00a0000', '1 234,5'])).toEqual([
      860_000, 860_000, 860_000, 1_234.5,
    ])
  })

  it('comma format (1,234,567.89) as before', () => {
    setDisplayFormats({ number_format: 'comma' })
    expect(readings(['860,000', '1,234.5', '860 000', '0.5'])).toEqual([
      860_000, 1_234.5, 860_000, 0.5,
    ])
  })

  it("refuses the other format's thousands, and marks that don't fit its own", () => {
    setDisplayFormats({ number_format: 'dot' })
    // 860 by the dot format's decimal comma, 860 thousand by the comma format's grouping.
    expect(readings(['860,000', '1,234', '1234.5', '1.23', '1.234,5.6'])).toEqual([
      null,
      null,
      null,
      null,
      null,
    ])
    setDisplayFormats({ number_format: 'comma' })
    expect(readings(['860.000', '1.234', '1234,5', '1.234.567'])).toEqual([null, null, null, null])
  })

  it('says which figure a refused thousandfold entry meant, in the format the user writes', () => {
    const shown = (text: string) => {
      render(
        <MoneyField
          aria-label="New value"
          value={16_000}
          formattedValue="16"
          onValueChange={vi.fn()}
        />,
      )
      const input = screen.getByRole('textbox', { name: 'New value' })
      fireEvent.focus(input)
      fireEvent.change(input, { target: { value: text } })
      fireEvent.blur(input)
      const message = screen.getByRole('alert').textContent
      cleanup()
      return message
    }
    setDisplayFormats({ number_format: 'dot' })
    expect(shown('17,000')).toBe('Did you mean 17.000?')
    expect(shown('S$ 17,000')).toBe('Did you mean 17.000?')
    setDisplayFormats({ number_format: 'comma' })
    expect(shown('17.000')).toBe('Did you mean 17,000?')
    expect(shown('about five')).toBe('Enter an amount of 0 or more.')
  })

  it('never reads a group that starts with 0 as thousands — no format writes one', () => {
    // Each is the other format's 0.5; reading it as 500 is a thousandfold error.
    setDisplayFormats({ number_format: 'comma' })
    expect(readings(['0,500', '00,500'])).toEqual([null, null])
    setDisplayFormats({ number_format: 'dot' })
    expect(readings(['0.500', '0,500'])).toEqual([null, 0.5])
    setDisplayFormats({ number_format: 'space' })
    expect(readings(['0 500'])).toEqual([null])
  })

  it("edits raw digits with the format's own decimal mark, and commits them unchanged", () => {
    setDisplayFormats({ number_format: 'dot' })
    const onValueChange = vi.fn()
    render(
      <MoneyField
        aria-label="Worth today"
        value={1_234.5}
        formattedValue="1.234,5"
        onValueChange={onValueChange}
      />,
    )
    const input = screen.getByRole('textbox', { name: 'Worth today' })
    fireEvent.focus(input)
    expect(input.getAttribute('value')).toBe('1234,5')
    fireEvent.blur(input)
    expect(onValueChange).toHaveBeenLastCalledWith(1_234.5)
  })

  it('opens formatted only when that text reads back as the value — else raw digits', () => {
    setDisplayFormats({ number_format: 'dot' })
    const { unmount } = render(
      <MoneyField
        aria-label="Value"
        autoFocus
        value={850_000}
        formattedValue="850.000"
        onValueChange={vi.fn()}
      />,
    )
    expect(screen.getByRole('textbox', { name: 'Value' }).getAttribute('value')).toBe('850.000')
    unmount()
    // Formatted for another locale: under the dot format "850,000" reads as 850.
    render(
      <MoneyField
        aria-label="Value"
        autoFocus
        value={850_000}
        formattedValue="850,000"
        onValueChange={vi.fn()}
      />,
    )
    expect(screen.getByRole('textbox', { name: 'Value' }).getAttribute('value')).toBe('850000')
  })
})

describe('MoneyField allowEmpty', () => {
  it('commits null when cleared on blur — blank is an answer, not an error', () => {
    const onValueChange = vi.fn()
    render(
      <MoneyField
        aria-label="Bought for"
        allowEmpty
        value={9_000}
        formattedValue="9,000"
        onValueChange={onValueChange}
      />,
    )
    const input = screen.getByRole('textbox', { name: 'Bought for' })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '  ' } })
    fireEvent.blur(input)
    expect(onValueChange).toHaveBeenCalledTimes(1)
    expect(onValueChange).toHaveBeenCalledWith(null)
    expect(input.getAttribute('aria-invalid')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('commits null once when cleared with Enter', () => {
    const onValueChange = vi.fn()
    render(
      <MoneyField
        aria-label="Bought for"
        allowEmpty
        value={9_000}
        formattedValue="9,000"
        onValueChange={onValueChange}
      />,
    )
    const input = screen.getByRole('textbox', { name: 'Bought for' })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.blur(input)
    expect(onValueChange).toHaveBeenCalledTimes(1)
    expect(onValueChange).toHaveBeenCalledWith(null)
  })

  it('starts blank with no value — never the text "null" — and commits what is typed', () => {
    const onValueChange = vi.fn()
    render(
      <MoneyField
        aria-label="Worth today"
        allowEmpty
        value={null}
        formattedValue=""
        prefix="S$"
        onValueChange={onValueChange}
      />,
    )
    const input = screen.getByRole('textbox', { name: 'Worth today' })
    expect(input.getAttribute('value')).toBe('')
    fireEvent.focus(input)
    expect(input.getAttribute('value')).toBe('')
    fireEvent.change(input, { target: { value: '14800' } })
    fireEvent.blur(input)
    expect(onValueChange).toHaveBeenCalledWith(14_800)
  })

  it('still refuses a negative or malformed amount', () => {
    const onValueChange = vi.fn()
    render(
      <MoneyField
        aria-label="Worth today"
        allowEmpty
        value={null}
        formattedValue=""
        onValueChange={onValueChange}
      />,
    )
    const input = screen.getByRole('textbox', { name: 'Worth today' })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '-5' } })
    fireEvent.blur(input)
    expect(onValueChange).not.toHaveBeenCalled()
    expect(input.getAttribute('aria-invalid')).toBe('true')
  })
})
