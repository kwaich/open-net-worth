// @vitest-environment jsdom
//
// Regression: the sidebar rendered a securities account as its HOLDINGS, dropping the
// settlement cash entirely. An `investment` account is worth positions PLUS settlement cash,
// and drawn margin is that cash gone negative (ADR 2026-09-07-seven-account-types-one-axis) —
// so the owner's brokerage read S$242,279 against a true S$231,010, and the S$11,269 margin
// debit was invisible on the Accounts tab.
//
// The three figures on screen are the group header, the venue subtotal and the account row.
// All three (and `useAccountsLedger`'s own two folds) go through `data.accountValue` now, so
// the definition exists once.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { Sidebar, type SidebarTotals } from './Sidebar'
import { fromApiAccount } from './data'
import type { ApiAccount } from './types'

const HOLDINGS = 242279
const SETTLEMENT_CASH = -11269

const totals: SidebarTotals = {
  cash: 0,
  fixed_deposit: 0,
  investment: 0,
  retirement: 0,
  receivable: 0,
  credit: 0,
  loan: 0,
  liabilities: 0,
}
const noop = vi.fn()
const accountsCss = readFileSync(resolve(__dirname, './accounts.css'), 'utf8')

function cssRule(selector: string): string {
  const at = accountsCss.indexOf(selector + ' {')
  expect(at, `missing rule for ${selector}`).toBeGreaterThan(-1)
  return accountsCss.slice(at, accountsCss.indexOf('}', at))
}

afterEach(() => cleanup())

function brokerage(id: string, name: string, holdings: number, settlement: number) {
  const api: ApiAccount = {
    id,
    name,
    kind: 'asset',
    type: 'investment',
    institution: 'IBKR',
    currency: 'SGD',
    balance: settlement,
    balance_base: settlement,
    cleared_balance: settlement,
    cleared_balance_base: settlement,
  }
  return fromApiAccount(api, holdings)
}

function renderSidebar(accounts: ReturnType<typeof brokerage>[]) {
  return render(
    <Sidebar
      accounts={accounts}
      groups={[
        {
          key: 'investment',
          label: 'Investments',
          color: '#3b86dd',
          accounts,
          totalBase: 0,
        },
      ]}
      totals={totals}
      selectedId={null}
      netWorth={HOLDINGS + SETTLEMENT_CASH}
      collapsed={false}
      onSelect={noop}
      onAdd={noop}
      onManagePayees={noop}
    />,
  )
}

/** Text of the elements matching `sel`, so an assertion targets ONE figure — the net-worth
 *  header above the groups shows the same total on this fixture. */
function textsOf(container: HTMLElement, sel: string): string[] {
  return Array.from(container.querySelectorAll(sel)).map((el) => el.textContent?.trim() ?? '')
}

describe('Sidebar — an investment account is positions PLUS settlement cash', () => {
  it('shows the group header net of the margin debit, not the raw holdings', () => {
    const { container } = renderSidebar([brokerage('acc-inv', 'IBKR', HOLDINGS, SETTLEMENT_CASH)])

    expect(textsOf(container, '.acct-grp-h .t')).toEqual(['S$231.01K'])
  })

  it('shows the account row net of the margin debit, and calls it value not holdings', () => {
    const { container } = renderSidebar([brokerage('acc-inv', 'IBKR', HOLDINGS, SETTLEMENT_CASH)])

    expect(container.querySelector('.acct-row--leaf')).toBeTruthy()
    expect(textsOf(container, '.acct-row-bal')).toEqual(['◈S$231.01K'])
    // The label has to move with the number: it is no longer the holdings figure.
    expect(textsOf(container, '.acct-row-cur')).toEqual(['value'])
  })

  it('sums a venue subtotal the same way', () => {
    // Two accounts at one institution collapse into a venue row (the >=2 rule).
    const { container } = renderSidebar([
      brokerage('acc-a', 'IBKR SGD', 200000, -11269),
      brokerage('acc-b', 'IBKR USD', 42279, 0),
    ])

    // The venue subtotal and the group header agree, both net of the debit.
    expect(textsOf(container, '.acct-grp-h .t')).toEqual(['S$231.01K'])
    expect(container.querySelector('.acct-row--venue')).toBeTruthy()
    expect(textsOf(container, '.acct-row-bal')[0]).toContain('S$231.01K')
  })

  it('aligns an expanded venue rail with its venue row without moving its child labels', () => {
    expect(cssRule('.acct-venue-kids')).toContain('margin-left: 20px')
    expect(cssRule('.acct-venue-kids .acct-row--leaf')).toContain('padding-inline-start: 8px')
  })
})
