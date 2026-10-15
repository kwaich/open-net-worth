// @vitest-environment jsdom
//
// The folder picker. A browser cannot open a native folder dialog for a server process, so
// the affordance is a path you type — checked before it is recorded, because a bad path
// accepted here fails at the NEXT boot, far from the action that caused it.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { StepWelcome } from './StepWelcome'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

function base(overrides = {}) {
  return {
    currency: 'SGD',
    onCurrencyChange: () => {},
    dataDir: '~/.open-net-worth/data',
    onNext: () => {},
    ...overrides,
  }
}

describe('StepWelcome — the working folder is stated, not asked', () => {
  it('shows where the data lives', () => {
    render(<StepWelcome {...base()} />)
    expect(screen.getByText('~/.open-net-worth/data')).toBeTruthy()
  })

  it('offers no way to change it from here', () => {
    // Setup used to offer this. The folder is resolved at process start and ~200 path
    // constants derive from it, so a change could not take effect until a restart — and
    // everything set AFTER it (password, model) was written to the OLD folder and lost on
    // that restart. The screen invited the one action that discarded the user's work.
    render(<StepWelcome {...base()} />)
    expect(screen.queryByRole('button', { name: /change data location/i })).toBeNull()
    expect(screen.queryByLabelText(/working folder path/i)).toBeNull()
  })

  it('in gateway mode states no folder and no machine-specific wording', () => {
    render(<StepWelcome {...base()} gatewayMode />)
    expect(screen.queryByText('~/.open-net-worth/data')).toBeNull()
    expect(screen.queryByText(/working folder/i)).toBeNull()
    expect(screen.queryByText(/this machine/i)).toBeNull()
    expect(screen.getByText(/app currency/i)).toBeTruthy()
  })

  it('says how to put it somewhere else instead of just refusing', () => {
    render(<StepWelcome {...base()} />)
    expect(screen.getByText(/ONW_ROOT/)).toBeTruthy()
  })

  it('says an existing book will be opened, not replaced', () => {
    render(
      <StepWelcome
        {...base({
          dataLocation: {
            has_book: true,
            accounts: 30,
            transactions: 2105,
            writable: true,
          },
        })}
      />,
    )
    expect(screen.getByText(/opened as it is, not replaced/i)).toBeTruthy()
    expect(screen.getByText(/30 accounts/)).toBeTruthy()
  })
})

describe('StepWelcome — both rows change the same way', () => {
  it('currency is the only thing this screen asks you to decide', () => {
    render(<StepWelcome {...base()} />)
    expect(screen.getByRole('button', { name: /change app currency/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /change data location/i })).toBeNull()
  })

  it('the currency picker only appears once Change is pressed', () => {
    render(<StepWelcome {...base()} />)
    expect(screen.getByText(/SGD/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /change app currency/i }))
    // The action gives way to the picker rather than sitting beside it.
    expect(screen.queryByRole('button', { name: /change app currency/i })).toBeNull()
  })

  it('explains what the app currency governs', () => {
    render(<StepWelcome {...base()} />)
    expect(screen.getByText(/can be in any currency/i)).toBeTruthy()
  })
})
