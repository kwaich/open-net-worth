// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AddCategoryModal } from './AddCategoryModal'

afterEach(() => cleanup())

describe('AddCategoryModal', () => {
  it('creates an income category without category-level tax controls or data', () => {
    const onSave = vi.fn()
    render(<AddCategoryModal open kind="income" onClose={vi.fn()} onSave={onSave} />)

    expect(screen.queryByText('Taxable')).toBeNull()
    fireEvent.change(screen.getByLabelText('Category name'), { target: { value: 'Salary' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith({ name: 'Salary', kind: 'income' })
  })
})
