// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConfirmDialog } from './ConfirmDialog'

afterEach(cleanup)

describe('ConfirmDialog', () => {
  it('opens on the safe choice — Cancel, not the ✕ or the destructive button', () => {
    render(
      <ConfirmDialog
        data={{
          title: 'Discard this asset?',
          body: "You'll lose what you've entered.",
          confirmLabel: 'Discard',
          cancelLabel: 'Keep editing',
          danger: true,
        }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Keep editing' }))
  })
})
