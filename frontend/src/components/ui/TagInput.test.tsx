// @vitest-environment jsdom

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { TagInput } from './TagInput'

afterEach(cleanup)

// Fires a paste event carrying `text` — jsdom has no real Clipboard, so we hand-build
// the minimal clipboardData shape TagInput's onPaste reads.
function paste(el: HTMLElement, text: string) {
  fireEvent.paste(el, { clipboardData: { getData: () => text } })
}

describe('TagInput — existing behavior (regression, additive-only paste)', () => {
  it('Enter adds the current draft as a tag', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<TagInput value={[]} onChange={onChange} />)
    const input = getByRole('textbox')
    fireEvent.change(input, { target: { value: 'japan-trip' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith(['japan-trip'])
  })

  it('comma adds the current draft as a tag', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<TagInput value={[]} onChange={onChange} />)
    const input = getByRole('textbox')
    fireEvent.change(input, { target: { value: 'reimbursable' } })
    fireEvent.keyDown(input, { key: ',' })
    expect(onChange).toHaveBeenCalledWith(['reimbursable'])
  })

  it('Backspace on an empty draft removes the last tag', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<TagInput value={['a', 'b']} onChange={onChange} />)
    fireEvent.keyDown(getByRole('textbox'), { key: 'Backspace' })
    expect(onChange).toHaveBeenCalledWith(['a'])
  })

  it('a single-token paste does NOT auto-add — falls through as normal text entry', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<TagInput value={[]} onChange={onChange} />)
    paste(getByRole('textbox'), 'AAPL')
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('TagInput — paste splitter (additive)', () => {
  it('splits a comma/whitespace block paste into multiple tags', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<TagInput value={[]} onChange={onChange} />)
    paste(getByRole('textbox'), 'AAPL, MSFT NVDA')
    expect(onChange).toHaveBeenCalledWith(['AAPL', 'MSFT', 'NVDA'])
  })

  it('dedupes pasted tokens against existing tags', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<TagInput value={['AAPL']} onChange={onChange} />)
    paste(getByRole('textbox'), 'AAPL, MSFT')
    expect(onChange).toHaveBeenCalledWith(['AAPL', 'MSFT'])
  })
})

describe('TagInput — max cap', () => {
  it('blocks an Enter-add past the cap and keeps the typed draft', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<TagInput value={['a', 'b']} onChange={onChange} max={2} />)
    const input = getByRole('textbox') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'c' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).not.toHaveBeenCalled()
    expect(input.value).toBe('c')
  })

  it('truncates a paste at the remaining room under the cap', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<TagInput value={['a']} onChange={onChange} max={2} />)
    paste(getByRole('textbox'), 'b c d')
    expect(onChange).toHaveBeenCalledWith(['a', 'b'])
  })
})
