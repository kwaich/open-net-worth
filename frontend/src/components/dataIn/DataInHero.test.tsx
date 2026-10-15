// @vitest-environment jsdom
//
// DataInHero is the shared zero-accounts view (Dashboard and Accounts): the design system's
// one empty state, the screen's own action first and Lucius's button second.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { DataInHero } from './DataInHero'

afterEach(cleanup)

function renderHero({ dock = true } = {}) {
  const onAddAccount = vi.fn()
  const onAskLucius = vi.fn()
  render(
    <DataInHero
      title="Start with the account you use most"
      body="Add an account to start your financial picture."
      onAddAccount={onAddAccount}
      luciusMessage="Help me set up my accounts"
      onAskLucius={dock ? onAskLucius : undefined}
    />,
  )
  return { onAddAccount, onAskLucius }
}

describe('DataInHero', () => {
  it('says what to do first, without a feature catalogue', () => {
    renderHero()
    expect(screen.getByText('Start with the account you use most')).toBeTruthy()
    expect(screen.getByText('Add an account to start your financial picture.')).toBeTruthy()
    expect(screen.queryByText('Start with')).toBeNull()
  })

  it('lists the screen own action before Lucius', () => {
    renderHero()
    const names = screen.getAllByRole('button').map((b) => b.textContent)
    expect(names).toEqual(['Add an account myself', 'Add with Lucius'])
  })

  it('fires the own action without asking Lucius', () => {
    const { onAddAccount, onAskLucius } = renderHero()
    fireEvent.click(screen.getByRole('button', { name: 'Add an account myself' }))
    expect(onAddAccount).toHaveBeenCalledTimes(1)
    expect(onAskLucius).not.toHaveBeenCalled()
  })

  it('sends the Lucius message without firing the own action', () => {
    const { onAddAccount, onAskLucius } = renderHero()
    fireEvent.click(screen.getByRole('button', { name: 'Add with Lucius' }))
    expect(onAskLucius).toHaveBeenCalledWith('Help me set up my accounts')
    expect(onAddAccount).not.toHaveBeenCalled()
  })

  it('offers no Lucius button when there is no chat dock', () => {
    renderHero({ dock: false })
    expect(screen.queryByRole('button', { name: 'Add with Lucius' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Add an account myself' })).toBeTruthy()
  })
})
