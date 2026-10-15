// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { takeSendIntent } from '../../../state/luciusSendIntent'
import { OutlookWhatIfAsk } from './OutlookWhatIfAsk'

const seen = vi.hoisted(() => ({ unseen: true, markSeen: vi.fn() }))
vi.mock('../../intro/useSeenIntro', () => ({ useSeenIntro: () => seen }))

afterEach(() => {
  cleanup()
  seen.unseen = true
  seen.markSeen.mockClear()
})

describe('OutlookWhatIfAsk', () => {
  it('asks Lucius a full question, to be sent, when a chip is clicked', () => {
    const ask = vi.fn()
    render(<OutlookWhatIfAsk onAskLucius={ask} />)
    fireEvent.click(screen.getByRole('button', { name: 'Retire at 55?' }))
    expect(ask).toHaveBeenCalledWith(
      'On my Outlook, what if I retire at 55? Move the sliders so I can see it.',
    )
    expect(takeSendIntent()).toBe(true)
  })

  it('tells the sheet a chip was picked so it can close', () => {
    const picked = vi.fn()
    render(<OutlookWhatIfAsk onAskLucius={vi.fn()} onPicked={picked} />)
    fireEvent.click(screen.getByRole('button', { name: 'Markets fall 30%?' }))
    expect(picked).toHaveBeenCalledTimes(1)
  })

  it('renders nothing without onAskLucius', () => {
    const { container } = render(<OutlookWhatIfAsk />)
    expect(container.firstChild).toBeNull()
  })

  it('goes for good once a chip is tapped', () => {
    render(<OutlookWhatIfAsk onAskLucius={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Retire at 55?' }))
    expect(seen.markSeen).toHaveBeenCalledTimes(1)
  })

  it('stays away once tapped before', () => {
    seen.unseen = false
    const { container } = render(<OutlookWhatIfAsk onAskLucius={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })
})
