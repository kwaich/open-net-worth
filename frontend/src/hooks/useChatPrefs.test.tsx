// @vitest-environment jsdom
//
// The two chat preferences behind Settings → AI → Chat.
//
// The DEFAULTS are the point of these tests. Follow-up suggestions cost a model call per
// turn, so nothing may opt you in implicitly — not an unset key, not a failed profile load.
// The cost chip is the opposite: spend stays visible unless you explicitly hide it.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import {
  CHAT_COST_VISIBLE_CHANGED_EVENT,
  useChatCostVisible,
  useChatSuggestionsEnabled,
} from './useChatSuggestionsEnabled'
import * as queryCache from '../state/queryCache'
import { PROFILE_KEY } from './serverState'

function Probe() {
  const { enabled } = useChatSuggestionsEnabled()
  const { visible } = useChatCostVisible()
  return (
    <div>
      <span data-testid="suggestions">{String(enabled)}</span>
      <span data-testid="cost">{String(visible)}</span>
    </div>
  )
}

function mockProfile(profile: unknown) {
  return vi.spyOn(queryCache, 'fetchCachedQuery').mockResolvedValue(profile as never)
}

beforeEach(() => vi.restoreAllMocks())
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('chat preferences', () => {
  it('leaves suggestions OFF when the profile has no key, and cost ON', async () => {
    mockProfile({})
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('suggestions').textContent).toBe('false'))
    expect(screen.getByTestId('cost').textContent).toBe('true')
  })

  it('turns suggestions on only for an explicit true', async () => {
    mockProfile({ chat_suggestions_enabled: true })
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('suggestions').textContent).toBe('true'))
  })

  it('does NOT enable suggestions when the profile fails to load', async () => {
    vi.spyOn(queryCache, 'fetchCachedQuery').mockRejectedValue(new Error('offline'))
    render(<Probe />)
    // The failure path must leave the paid feature off, not fall back to "on".
    await waitFor(() => expect(screen.getByTestId('suggestions').textContent).toBe('false'))
  })

  it('hides the cost chip only for an explicit false', async () => {
    mockProfile({ chat_cost_visible: false })
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('cost').textContent).toBe('false'))
  })

  it('re-reads the cost preference when its change event fires', async () => {
    const spy = mockProfile({ chat_cost_visible: true })
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('cost').textContent).toBe('true'))

    spy.mockResolvedValue({ chat_cost_visible: false } as never)
    window.dispatchEvent(new Event(CHAT_COST_VISIBLE_CHANGED_EVENT))
    // An already-open chat panel must reflect the switch without a reload.
    await waitFor(() => expect(screen.getByTestId('cost').textContent).toBe('false'))
  })
})

describe('a cached profile is honoured from the FIRST render', () => {
  // With "Show cost per turn" off, every chip used to render on the default, then vanish a
  // tick later when the profile arrived — collapsing rows and jumping the conversation.
  it('cost OFF in the cached profile is OFF before any fetch resolves', () => {
    queryCache.setCachedQueryData(PROFILE_KEY, { chat_cost_visible: false })
    vi.spyOn(queryCache, 'fetchCachedQuery').mockReturnValue(new Promise(() => {}))
    render(<Probe />)
    expect(screen.getByTestId('cost').textContent).toBe('false')
    queryCache.deleteCachedQueryData(PROFILE_KEY)
  })
})
