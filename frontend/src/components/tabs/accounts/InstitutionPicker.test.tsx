// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { InstitutionPicker } from './ds'

const options = [
  { id: 'inst_dbs', name: 'DBS', accountCount: 2 },
  { id: 'inst_ibkr', name: 'IBKR', accountCount: 1 },
]

afterEach(cleanup)

describe('InstitutionPicker', () => {
  it('shows saved institutions on focus and identifies itself as a combobox', () => {
    render(<InstitutionPicker value="" options={options} onChange={vi.fn()} />)

    const input = screen.getByRole('combobox')
    expect(input.getAttribute('aria-expanded')).toBe('false')
    fireEvent.focus(input)

    expect(input.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('option', { name: 'DBS' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'IBKR' })).toBeTruthy()
  })

  it('describes an unsaved value honestly instead of claiming an institution was created', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <InstitutionPicker value="UOB" options={options.slice(0, 1)} onChange={onChange} />,
    )

    fireEvent.focus(screen.getByRole('combobox'))
    fireEvent.mouseDown(screen.getByRole('option', { name: 'Use “UOB”' }))
    expect(onChange).toHaveBeenCalledWith('UOB')

    rerender(<InstitutionPicker value="UOB" options={options.slice(0, 1)} onChange={onChange} />)
    expect(screen.getByText('Will be saved with this account')).toBeTruthy()
    expect(screen.queryByText('New institution created')).toBeNull()
  })

  it('shows an explicit empty state when there are no saved institutions', () => {
    render(<InstitutionPicker value="" options={[]} onChange={vi.fn()} />)
    fireEvent.focus(screen.getByRole('combobox'))
    expect(screen.getByText('No saved institutions yet')).toBeTruthy()
  })

  it('opens management from a control outside the selectable options', () => {
    const onManage = vi.fn()
    render(<InstitutionPicker value="" options={options} onChange={vi.fn()} onManage={onManage} />)
    fireEvent.focus(screen.getByRole('combobox'))
    fireEvent.mouseDown(screen.getByRole('button', { name: 'Manage saved institutions' }))
    expect(onManage).toHaveBeenCalledOnce()
  })
})
