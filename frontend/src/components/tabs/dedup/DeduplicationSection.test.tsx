// @vitest-environment jsdom
//
// Unit tests for UX-1 JudgeResultNotice rendering states:
//   - counts copy (n merged, m kept), cost_capped warn line, dismiss.
// Also tests UX-2 Forget flow in DeduplicationPairRow.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import { JudgeResultNotice } from './DeduplicationSection'
import { DeduplicationPairRow } from './DeduplicationPairRow'

// ── Mock api/matches ──────────────────────────────────────────────────────────

vi.mock('../../../api/matches', async (importActual) => {
  const actual = await importActual<typeof import('../../../api/matches')>()
  return {
    ...actual,
    forgetMatchDecision: vi.fn(),
    swapCanonical: vi.fn(),
  }
})

import { forgetMatchDecision } from '../../../api/matches'
const mockForget = forgetMatchDecision as ReturnType<typeof vi.fn>

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeReview(
  overrides: Partial<import('../../../api/matches').MatchReview> = {},
): import('../../../api/matches').MatchReview {
  return {
    id: 'rev-1',
    klass: 'near_duplicate' as const,
    score: 0.9,
    become_transfer: false,
    reasons: [],
    llm_reason: null,
    batch_id: null,
    batch_description: null,
    batch_created_at: null,
    status: 'dismissed' as const,
    signature: 'sig-abc',
    incoming: {
      id: 'txn-in',
      date: '2026-06-01',
      description: 'Incoming txn',
      amount: 100,
      currency: 'SGD',
      kind: 'expense',
      from_account_id: null,
      to_account_id: null,
      source: null,
      external_refs: [],
    },
    canonical: {
      id: 'txn-can',
      date: '2026-06-01',
      description: 'Existing txn',
      amount: 100,
      currency: 'SGD',
      kind: 'expense',
      from_account_id: null,
      to_account_id: null,
      source: null,
      external_refs: [],
    },
    ...overrides,
  }
}

// ── JudgeResultNotice tests ───────────────────────────────────────────────────

describe('JudgeResultNotice', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('shows merged count and kept count copy (n > 0)', () => {
    const onDismiss = vi.fn()
    render(
      <JudgeResultNotice
        result={{ auto_merged: 3, kept_for_review: 2, cost_capped: false }}
        onDismiss={onDismiss}
      />,
    )
    expect(
      screen.getByText(/Merged 3 transfer pairs\. 2 matches kept for your review\./),
    ).toBeTruthy()
  })

  it('shows singular "pair" when n === 1', () => {
    render(
      <JudgeResultNotice
        result={{ auto_merged: 1, kept_for_review: 0, cost_capped: false }}
        onDismiss={() => {}}
      />,
    )
    expect(screen.getByText(/Merged 1 transfer pair\. 0 matches kept/)).toBeTruthy()
  })

  it('shows "No pairs were auto-merged" when n === 0', () => {
    render(
      <JudgeResultNotice
        result={{ auto_merged: 0, kept_for_review: 5, cost_capped: false }}
        onDismiss={() => {}}
      />,
    )
    expect(
      screen.getByText(/No pairs were auto-merged\. 5 matches kept for your review\./),
    ).toBeTruthy()
  })

  it('shows singular "match" when m === 1', () => {
    render(
      <JudgeResultNotice
        result={{ auto_merged: 2, kept_for_review: 1, cost_capped: false }}
        onDismiss={() => {}}
      />,
    )
    expect(
      screen.getByText(/Merged 2 transfer pairs\. 1 match kept for your review\./),
    ).toBeTruthy()
  })

  it('does NOT render the cost_capped warn line when cost_capped is false', () => {
    render(
      <JudgeResultNotice
        result={{ auto_merged: 2, kept_for_review: 1, cost_capped: false }}
        onDismiss={() => {}}
      />,
    )
    expect(screen.queryByText(/AI cap reached/)).toBeNull()
  })

  it('renders the cost_capped warn line when cost_capped is true', () => {
    render(
      <JudgeResultNotice
        result={{ auto_merged: 2, kept_for_review: 3, cost_capped: true }}
        onDismiss={() => {}}
      />,
    )
    expect(screen.getByText('AI cap reached — some pairs need manual review.')).toBeTruthy()
  })

  it('calls onDismiss when Dismiss button is clicked', () => {
    const onDismiss = vi.fn()
    render(
      <JudgeResultNotice
        result={{ auto_merged: 1, kept_for_review: 0, cost_capped: false }}
        onDismiss={onDismiss}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Dismiss/ }))
    expect(onDismiss).toHaveBeenCalledOnce()
  })
})

// ── UX-2 Forget flow in DeduplicationPairRow ─────────────────────────────────

describe('DeduplicationPairRow — Forget flow (UX-2)', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  const noop = async () => {}
  const noopAccept = async () => ({ error: null, canonicalDescription: null })
  const noopUndo = async () => null

  it('shows "Dismissed — remembered as different." copy after dismiss', async () => {
    const onDismiss = vi.fn().mockResolvedValue(null)
    render(
      <DeduplicationPairRow
        review={makeReview()}
        accountNames={new Map()}
        onAccept={noopAccept}
        onDismiss={onDismiss}
        onUndo={noopUndo}
        onReload={noop}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Dismiss/ }))
    await waitFor(() => {
      expect(screen.getByText('Dismissed — remembered as different.')).toBeTruthy()
    })
  })

  it('shows Forget button alongside Undo in dismissed state', async () => {
    const onDismiss = vi.fn().mockResolvedValue(null)
    render(
      <DeduplicationPairRow
        review={makeReview()}
        accountNames={new Map()}
        onAccept={noopAccept}
        onDismiss={onDismiss}
        onUndo={noopUndo}
        onReload={noop}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Dismiss/ }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Forget/ })).toBeTruthy()
      expect(screen.getByRole('button', { name: /Undo/ })).toBeTruthy()
    })
  })

  it('transitions to "forgotten" state on successful Forget', async () => {
    const onDismiss = vi.fn().mockResolvedValue(null)
    mockForget.mockResolvedValue({ ok: true })
    render(
      <DeduplicationPairRow
        review={makeReview()}
        accountNames={new Map()}
        onAccept={noopAccept}
        onDismiss={onDismiss}
        onUndo={noopUndo}
        onReload={noop}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Dismiss/ }))
    await waitFor(() => screen.getByRole('button', { name: /Forget/ }))
    fireEvent.click(screen.getByRole('button', { name: /Forget/ }))
    await waitFor(() => {
      expect(
        screen.getByText(/Decision cleared — the engine will re-evaluate this pair/),
      ).toBeTruthy()
    })
    // No Forget or Undo button in forgotten state
    expect(screen.queryByRole('button', { name: /Forget/ })).toBeNull()
  })

  it('shows inline error and stays in dismissed state when Forget fails', async () => {
    const onDismiss = vi.fn().mockResolvedValue(null)
    mockForget.mockRejectedValue(new Error('API error'))
    render(
      <DeduplicationPairRow
        review={makeReview()}
        accountNames={new Map()}
        onAccept={noopAccept}
        onDismiss={onDismiss}
        onUndo={noopUndo}
        onReload={noop}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Dismiss/ }))
    await waitFor(() => screen.getByRole('button', { name: /Forget/ }))
    fireEvent.click(screen.getByRole('button', { name: /Forget/ }))
    await waitFor(() => {
      expect(screen.getByText('API error')).toBeTruthy()
    })
    // Still in dismissed state — Forget button still present
    expect(screen.getByRole('button', { name: /Forget/ })).toBeTruthy()
    expect(screen.getByText('Dismissed — remembered as different.')).toBeTruthy()
  })
})
