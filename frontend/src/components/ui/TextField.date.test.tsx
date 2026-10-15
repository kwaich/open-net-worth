// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TextField } from './TextField'

afterEach(cleanup)

describe('TextField date containment', () => {
  it('keeps the native date input tappable inside an overflow-clipping wrapper', () => {
    const onChange = vi.fn()
    render(
      <TextField
        aria-label="Vest date"
        type="date"
        value="2026-09-15"
        data-autofocus
        onChange={onChange}
      />,
    )

    const input = screen.getByLabelText('Vest date')
    const wrapper = input.parentElement

    expect(input).toHaveProperty('type', 'date')
    expect(input.classList.contains('ui-input')).toBe(true)
    expect(input.hasAttribute('data-autofocus')).toBe(true)
    expect(wrapper?.classList.contains('ui-datefield')).toBe(true)

    fireEvent.change(input, { target: { value: '2026-09-16' } })
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('does not wrap ordinary text inputs', () => {
    render(<TextField aria-label="Grant symbol" value="KSTL" readOnly />)

    expect(
      screen.getByLabelText('Grant symbol').parentElement?.classList.contains('ui-datefield'),
    ).toBe(false)
  })
})
