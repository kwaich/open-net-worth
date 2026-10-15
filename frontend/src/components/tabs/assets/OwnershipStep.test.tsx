// @vitest-environment jsdom
//
// OwnershipStep — the wizard's step 3 and, verbatim, the asset page's Ownership editor.
// Synthetic fixtures only. Radix is not involved (no Modal here), so queries use `screen`.
import { useRef, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { AssetLoan } from '../../../types/assets'
import { setBaseCurrency } from '../../../utils/format'
import { EMPTY_OWNERSHIP, NEW_LOAN, withOwnership, type OwnershipValue } from './assetForms'
import { fmt } from './assetsData'
import { OwnershipStep, type OwnershipStepProps } from './OwnershipStep'

setBaseCurrency('SGD')
afterEach(cleanup)

const FREE: AssetLoan = {
  id: 'acc_free01',
  name: 'Test Mortgage',
  balance: 310000,
  rate_label: '3.1%',
  term_label: 'to 2044',
  held_by: null,
}
const HELD: AssetLoan = {
  id: 'acc_held01',
  name: 'Test Car Loan',
  balance: 38000,
  rate_label: '',
  term_label: '',
  held_by: { id: 'oa_holder01', name: 'Test Condo' },
}

function Harness({
  initial = EMPTY_OWNERSHIP,
  onValue,
  ...props
}: Partial<Omit<OwnershipStepProps, 'value' | 'onChange'>> & {
  initial?: OwnershipValue
  onValue?: (v: OwnershipValue) => void
}) {
  // Merges each patch into the newest value, as the wizard and the Ownership editor do.
  const [value, setValue] = useState(initial)
  const latest = useRef(initial)
  return (
    <OwnershipStep
      category="property"
      loans={[FREE, HELD]}
      assetId={null}
      currency="SGD"
      errors={{}}
      {...props}
      value={value}
      onChange={(patch) => {
        latest.current = withOwnership(latest.current, patch)
        setValue(latest.current)
        onValue?.(latest.current)
      }}
    />
  )
}

function typeMoney(label: string, text: string) {
  const input = screen.getByRole('textbox', { name: label })
  fireEvent.focus(input)
  fireEvent.change(input, { target: { value: text } })
  fireEvent.blur(input)
}

function loanSelect(): HTMLSelectElement {
  return screen.getByRole('combobox', { name: 'Any loan against it?' }) as HTMLSelectElement
}

describe('OwnershipStep — share', () => {
  it('defaults to All mine, focused, and asks nothing more for a watch', () => {
    render(<Harness category="watch" />)
    const mine = screen.getByRole('tab', { name: 'All mine' })
    expect(mine.getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(mine)
    expect(screen.queryByRole('textbox', { name: 'Your share' })).toBeNull()
    expect(screen.queryByText('Any loan against it?')).toBeNull()
  })

  it('Shared reveals Your share at 50 — never blank, never 100 — and who with', () => {
    const onValue = vi.fn()
    render(<Harness category="watch" onValue={onValue} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Shared' }))
    expect(onValue).toHaveBeenLastCalledWith({ ...EMPTY_OWNERSHIP, shared: true, sharePct: 50 })
    expect(screen.getByRole('textbox', { name: 'Your share' }).getAttribute('value')).toBe('50')
    expect(screen.getByText('Net worth counts only your share.')).toBeTruthy()
    const withWhom = screen.getByRole('textbox', { name: 'Shared with (optional)' })
    expect(withWhom.getAttribute('placeholder')).toBe('e.g. joint with Alex')
    fireEvent.change(withWhom, { target: { value: 'Joint with A. Lim' } })
    typeMoney('Your share', '60')
    expect(onValue).toHaveBeenLastCalledWith({
      ...EMPTY_OWNERSHIP,
      shared: true,
      sharePct: 60,
      shareNote: 'Joint with A. Lim',
    })
  })

  it('shows a share error beside the share field', () => {
    render(
      <Harness
        category="watch"
        initial={{ ...EMPTY_OWNERSHIP, shared: true, sharePct: 150 }}
        errors={{ share_pct: 'Enter a share from 0 to 100.' }}
      />,
    )
    expect(screen.getByRole('alert').textContent).toBe('Enter a share from 0 to 100.')
  })
})

describe('OwnershipStep — loan', () => {
  it("keeps the asset's own loan selectable when an older asset holds it too (legacy double link)", () => {
    render(
      <Harness
        assetId="oa_mine00001"
        initial={{ ...EMPTY_OWNERSHIP, loanId: HELD.id }}
        loans={[FREE, HELD]}
      />,
    )
    const own = within(loanSelect()).getByRole('option', {
      name: /Test Car Loan/,
    }) as HTMLOptionElement
    expect(own.disabled).toBe(false)
    expect(own.textContent).toBe(`Test Car Loan · ${fmt(38000)} (also secures Test Condo)`)
    expect(loanSelect().value).toBe(HELD.id)
  })

  it('shows a linked loan that is no longer an active account, never "No loan"', () => {
    render(
      <Harness
        assetId="oa_mine00001"
        initial={{ ...EMPTY_OWNERSHIP, loanId: 'acc_archived1' }}
        loans={[FREE]}
      />,
    )
    expect(loanSelect().value).toBe('acc_archived1')
    expect(loanSelect().selectedOptions[0]?.textContent).toBe('Linked loan (no longer active)')
  })

  it('asks about a loan for property, vehicles and custom categories only', () => {
    const { unmount } = render(<Harness category="c_boats" />)
    expect(loanSelect()).toBeTruthy()
    unmount()
    render(<Harness category="art" />)
    expect(screen.queryByText('Any loan against it?')).toBeNull()
  })

  it('still shows a loan an asset already has, so it can be unlinked', () => {
    render(<Harness category="watch" initial={{ ...EMPTY_OWNERSHIP, loanId: FREE.id }} />)
    expect(loanSelect().value).toBe(FREE.id)
  })

  it('lists No loan, each loan with its balance, and a held loan disabled with its holder', () => {
    render(<Harness />)
    const options = within(loanSelect()).getAllByRole('option') as HTMLOptionElement[]
    expect(options.map((o) => o.textContent)).toEqual([
      'No loan',
      `Test Mortgage · ${fmt(310000)}`,
      `Test Car Loan · ${fmt(38000)} (secures Test Condo)`,
    ])
    expect(options.map((o) => o.disabled)).toEqual([false, false, true])
  })

  it("keeps the asset's own loan selectable in its editor", () => {
    render(<Harness assetId="oa_holder01" />)
    const own = within(loanSelect()).getByRole('option', {
      name: `Test Car Loan · ${fmt(38000)}`,
    }) as HTMLOptionElement
    expect(own.disabled).toBe(false)
  })

  it('shows the server reason under the loan select', () => {
    render(
      <Harness
        errors={{
          loan_account_id:
            'Test Mortgage already secures “Test Condo”. A loan can secure one asset — unlink it there first.',
        }}
      />,
    )
    expect(screen.getByRole('alert').textContent).toContain('already secures “Test Condo”')
  })

  it('with no loan accounts, says so above the add-a-loan trigger', () => {
    render(<Harness loans={[]} />)
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.getByText('No loan accounts yet.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Add a loan' })).toBeTruthy()
  })
})

describe('OwnershipStep — adding a loan', () => {
  it('stages the loan — selected, marked new, created only on save', () => {
    const onValue = vi.fn()
    render(<Harness loans={[]} onValue={onValue} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add a loan' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Loan name' }), {
      target: { value: 'Test Car Loan' },
    })
    typeMoney('Amount owed', '38000')
    fireEvent.click(screen.getByRole('button', { name: 'Add loan' }))

    expect(onValue).toHaveBeenLastCalledWith({
      ...EMPTY_OWNERSHIP,
      loanId: NEW_LOAN,
      newLoan: { name: 'Test Car Loan', owed: 38000 },
    })
    expect(screen.queryByRole('textbox', { name: 'Loan name' })).toBeNull()
    expect(loanSelect().value).toBe(NEW_LOAN)
    expect(loanSelect().selectedOptions[0]?.textContent).toBe(`Test Car Loan · ${fmt(38000)} (new)`)
    expect(screen.getByText('Added to Accounts when you save.')).toBeTruthy()
    // Staged, the trigger edits that loan rather than adding another.
    fireEvent.click(screen.getByRole('button', { name: 'Edit new loan' }))
    expect(screen.getByText('New loan account')).toBeTruthy()
    expect((screen.getByRole('textbox', { name: 'Loan name' }) as HTMLInputElement).value).toBe(
      'Test Car Loan',
    )
    expect(screen.getByRole('button', { name: 'Add loan' }).className).toContain('btn-secondary')
  })

  it('says what the new loan becomes before anything is added', () => {
    render(<Harness loans={[]} onValue={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add a loan' }))
    expect(
      screen.getByText(
        'Added to Accounts as a loan when you save — what you owe today lowers your net worth.',
      ),
    ).toBeTruthy()
  })

  it('lists the staged loan after the existing ones', () => {
    render(
      <Harness
        initial={{ ...EMPTY_OWNERSHIP, loanId: NEW_LOAN, newLoan: { name: 'New', owed: 5 } }}
      />,
    )
    const texts = within(loanSelect())
      .getAllByRole('option')
      .map((o) => o.textContent)
    expect(texts[texts.length - 1]).toBe(`New · ${fmt(5)} (new)`)
  })

  it('refuses a blank name or amount, beside each field', () => {
    const onValue = vi.fn()
    render(<Harness onValue={onValue} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add a loan' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add loan' }))
    expect(screen.getByText('Enter a name for this loan.')).toBeTruthy()
    expect(screen.getByText('Enter an amount greater than 0.')).toBeTruthy()
    expect(onValue).not.toHaveBeenCalled()
  })

  it('Enter in the amount adds the loan — and does not reach the step around it', () => {
    const outer = vi.fn()
    const onValue = vi.fn()
    render(
      <div onKeyDown={outer}>
        <Harness onValue={onValue} />
      </div>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add a loan' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Loan name' }), {
      target: { value: 'Test Car Loan' },
    })
    const owed = screen.getByRole('textbox', { name: 'Amount owed' })
    fireEvent.focus(owed)
    fireEvent.change(owed, { target: { value: '38000' } })
    fireEvent.keyDown(owed, { key: 'Enter' })
    expect(onValue).toHaveBeenLastCalledWith(
      expect.objectContaining({ newLoan: { name: 'Test Car Loan', owed: 38000 } }),
    )
    expect(outer).not.toHaveBeenCalled()
  })

  it('Cancel closes the form and stages nothing', () => {
    const onValue = vi.fn()
    render(<Harness onValue={onValue} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add a loan' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Loan name' }), {
      target: { value: 'Abandoned' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('textbox', { name: 'Loan name' })).toBeNull()
    expect(onValue).not.toHaveBeenCalled()
  })

  it("reopens a refused staged loan with the server's reason beside the field", () => {
    render(
      <Harness
        initial={{
          ...EMPTY_OWNERSHIP,
          loanId: NEW_LOAN,
          newLoan: { name: 'Test Car Loan', owed: 38000 },
        }}
        errors={{ loan_name: 'Loan name: String should have at most 120 characters' }}
      />,
    )
    expect((screen.getByRole('textbox', { name: 'Loan name' }) as HTMLInputElement).value).toBe(
      'Test Car Loan',
    )
    expect(screen.getByRole('alert').textContent).toBe(
      'Loan name: String should have at most 120 characters',
    )
  })
})
