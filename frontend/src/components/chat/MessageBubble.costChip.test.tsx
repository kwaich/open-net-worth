// @vitest-environment jsdom
//
// The per-turn cost chip obeys Settings → AI → Chat → "Show cost per turn".
//
// The check lives INSIDE CostChip rather than at its call site, so every place that renders a
// chip honours the preference without each one remembering to ask. These tests pin that: the
// gate is the component's own, and hiding it is purely presentational — nothing about the
// recorded usage changes.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import * as prefs from '../../hooks/useChatSuggestionsEnabled'
import { CostChip } from './MessageBubble'

const USAGE = {
  input_tokens: 1200,
  output_tokens: 340,
  cache_read_tokens: 0,
  cache_creation_tokens: 0,
  cost_usd: 0.262,
  model: 'anthropic:claude-sonnet-4-6',
}

beforeEach(() => vi.restoreAllMocks())
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('CostChip visibility', () => {
  it('renders the tokens, price and model when the preference is on', async () => {
    vi.spyOn(prefs, 'useChatCostVisible').mockReturnValue({ visible: true, loaded: true })
    render(<CostChip usage={USAGE} />)
    await waitFor(() => expect(screen.getByText(/\$0\.262/)).toBeTruthy())
    expect(screen.getByText(/1,200 in/)).toBeTruthy()
    expect(screen.getByText(/sonnet-4-6/)).toBeTruthy()
  })

  it('renders NOTHING when the preference is off', () => {
    vi.spyOn(prefs, 'useChatCostVisible').mockReturnValue({ visible: false, loaded: true })
    const { container } = render(<CostChip usage={USAGE} />)
    expect(container.innerHTML).toBe('')
  })
})

describe('CostChip unpriced model', () => {
  it('says the price is unknown instead of showing <$0.001', async () => {
    vi.spyOn(prefs, 'useChatCostVisible').mockReturnValue({ visible: true, loaded: true })
    render(<CostChip usage={{ ...USAGE, cost_usd: 0, price_known: false, model: 'gpt-x' }} />)
    await waitFor(() => expect(screen.getByText(/price unknown/)).toBeTruthy())
    expect(screen.queryByText(/<\$0\.001/)).toBeNull()
  })
})

describe('CostChip never flashes on a guess', () => {
  it('renders nothing while the preference is still unknown', () => {
    vi.spyOn(prefs, 'useChatCostVisible').mockReturnValue({ visible: true, loaded: false })
    const { container } = render(<CostChip usage={USAGE} />)
    expect(container.innerHTML).toBe('')
  })
})
