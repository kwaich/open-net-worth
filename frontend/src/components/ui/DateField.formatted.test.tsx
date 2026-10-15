// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setDisplayFormats } from '../../utils/format'
import { DateField } from './DateField'

afterEach(() => {
  cleanup()
  setDisplayFormats({})
})

describe('DateField with a Date-format preference', () => {
  it('is the native input on Browser default', () => {
    render(<DateField aria-label="Birth" value="1998-05-24" onChange={() => {}} />)
    expect(screen.getByLabelText('Birth')).toHaveProperty('type', 'date')
  })

  it('shows the stored day in the chosen format', () => {
    setDisplayFormats({ date_format: 'mdy' })
    render(<DateField aria-label="Birth" value="1998-05-24" onChange={() => {}} />)
    expect((screen.getByLabelText('Birth') as HTMLInputElement).value).toBe('05/24/1998')
    cleanup()
    setDisplayFormats({ date_format: 'dmy' })
    render(<DateField aria-label="Birth" value="1998-05-24" onChange={() => {}} />)
    expect((screen.getByLabelText('Birth') as HTMLInputElement).value).toBe('24/05/1998')
  })

  it('reads typing in that order and hands the caller an ISO value', () => {
    setDisplayFormats({ date_format: 'dmy' })
    const onChange = vi.fn()
    render(<DateField aria-label="Birth" value="" onChange={onChange} />)
    const input = screen.getByLabelText('Birth')

    fireEvent.change(input, { target: { value: '24/05/19' } })
    expect(onChange).not.toHaveBeenCalled()

    fireEvent.change(input, { target: { value: '24/05/1998' } })
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0]![0].target.value).toBe('1998-05-24')
  })

  it('flags an impossible date and drops it on blur', () => {
    setDisplayFormats({ date_format: 'dmy' })
    const onChange = vi.fn()
    render(<DateField aria-label="Birth" value="1998-05-24" onChange={onChange} />)
    const input = screen.getByLabelText('Birth') as HTMLInputElement

    fireEvent.change(input, { target: { value: '31/02/1998' } })
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(onChange).not.toHaveBeenCalled()

    fireEvent.blur(input)
    expect(input.value).toBe('24/05/1998')
  })

  it('clears the value when the text is emptied', () => {
    setDisplayFormats({ date_format: 'iso' })
    const onChange = vi.fn()
    render(<DateField aria-label="Birth" value="1998-05-24" onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('Birth'), { target: { value: '' } })
    expect(onChange.mock.calls[0]![0].target.value).toBe('')
  })

  it('takes a date from the calendar picker', () => {
    setDisplayFormats({ date_format: 'dmy' })
    const onChange = vi.fn()
    const { container } = render(
      <DateField aria-label="Birth" value="" min="1990-01-01" onChange={onChange} />,
    )
    const native = container.querySelector('input[type="date"]') as HTMLInputElement
    expect(native.min).toBe('1990-01-01')
    fireEvent.change(native, { target: { value: '2001-02-03' } })
    expect(onChange.mock.calls[0]![0].target.value).toBe('2001-02-03')
  })
})
