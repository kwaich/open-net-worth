// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AccountModal from './AccountModal'
import { TYPE_LABELS, TYPE_MEANING } from './categories'

afterEach(cleanup)

function renderTypeStep() {
  render(<AccountModal open mode="add" institutions={[]} onClose={() => {}} onSave={vi.fn()} />)
  fireEvent.click(screen.getByLabelText(/Account type:/))
}

describe('AccountModal type picker', () => {
  it('shows seven rows with their meaning and no band/locked/on-budget controls', () => {
    renderTypeStep()
    for (const t of Object.keys(TYPE_LABELS) as (keyof typeof TYPE_LABELS)[]) {
      expect(screen.getByText(TYPE_LABELS[t])).toBeTruthy()
      expect(screen.getByText(TYPE_MEANING[t])).toBeTruthy()
    }
    expect(screen.queryByText(/Net-worth band/)).toBeNull()
    expect(screen.queryByText(/Locked/)).toBeNull()
    expect(screen.queryByRole('tab', { name: 'On budget' })).toBeNull()
  })
})
