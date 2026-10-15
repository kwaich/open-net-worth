// @vitest-environment jsdom
//
// ActivityPanel — the Lucius landing screen's Active/Scheduled sections. Covers the
// acceptance criteria from docs/qa/acceptance/lucius-activity-panel.md: renders nothing when
// both sections are empty, rows render with the right subtitle/status, Review appears only on
// a `waiting` row, a row (or its Review button) navigates via onOpenAutomation, and the
// "Show more"/"Show less" toggle works.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ActivityPanel } from './ActivityPanel'
import type { AutomationActivity } from '../../../api/automations'
import type { QueryCacheResult } from '../../../state/queryCache'

const activity = vi.fn<() => QueryCacheResult<AutomationActivity>>()
const authFetch = vi.fn()
let ledgerVersion = 0
vi.mock('../../../hooks/useAutomationActivity', () => ({
  useAutomationActivity: () => activity(),
}))
vi.mock('../../../api/client', () => ({
  authFetch: (...args: unknown[]) => authFetch(...args),
}))
vi.mock('../../../hooks/useDataVersion', () => ({
  useDataVersion: () => ledgerVersion,
}))

function okJson(body: unknown): Response {
  return {
    ok: true,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response
}

function mockActivity(data: AutomationActivity | undefined) {
  activity.mockReturnValue({
    data,
    error: null,
    initialLoading: data === undefined,
    refreshing: false,
    refresh: vi.fn(),
  })
}

beforeEach(() => {
  activity.mockReset()
  authFetch.mockReset()
  ledgerVersion = 0
  authFetch.mockResolvedValue(okJson({ transactions: [], total: 0, has_more: false }))
})
afterEach(cleanup)

describe('ActivityPanel', () => {
  it('groups a transaction backlog into one Needs attention item', async () => {
    mockActivity({ active: [], scheduled: [] })
    authFetch.mockResolvedValue(
      okJson({ transactions: [{ id: 'txn-1' }], total: 7, has_more: true }),
    )
    const onOpenTransactionReview = vi.fn()

    render(
      <ActivityPanel
        onOpenAutomation={vi.fn()}
        onOpenTransactionReview={onOpenTransactionReview}
      />,
    )

    expect(await screen.findByText('Needs attention')).toBeTruthy()
    const section = screen.getByText('Needs attention').closest('section')
    expect(section).toBeTruthy()
    expect(within(section as HTMLElement).getByText('1')).toBeTruthy()
    expect(within(section as HTMLElement).getByText('7 transactions to review')).toBeTruthy()
    expect(within(section as HTMLElement).getAllByRole('button')).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: 'Review transactions' }))
    expect(onOpenTransactionReview).toHaveBeenCalledTimes(1)
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith(
        '/api/transactions/recently-added?needs_review=true&limit=1',
      ),
    )
  })

  it('keeps Needs attention visible on mobile while automation sections stay hidden', async () => {
    mockActivity({
      active: [
        {
          automation_id: 'auto_1',
          automation_name: 'Daily sync',
          run_id: 'run_1',
          status: 'running',
          started_at: new Date().toISOString(),
        },
      ],
      scheduled: [
        {
          automation_id: 'auto_2',
          automation_name: 'Weekly review',
          next_run_at: new Date().toISOString(),
        },
      ],
    })
    authFetch.mockResolvedValue(okJson({ transactions: [{ id: 'txn-1' }], total: 7 }))

    render(
      <ActivityPanel
        onOpenAutomation={vi.fn()}
        onOpenTransactionReview={vi.fn()}
        showAutomationSections={false}
      />,
    )

    expect(await screen.findByText('7 transactions to review')).toBeTruthy()
    expect(screen.queryByText('Active')).toBeNull()
    expect(screen.queryByText('Scheduled')).toBeNull()
  })

  it('lists a failed automation under Needs attention, on the phone too', async () => {
    const failedRun = {
      automation_id: 'auto_1',
      automation_name: 'Log trades from email',
      run_id: 'run_1',
      session_id: 'automation-run-run_1',
      status: 'failed' as const,
      started_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
      error: "Lucius didn't answer in time. Try again.",
    }
    mockActivity({ active: [failedRun], scheduled: [], failed: [failedRun] })
    const onOpenAutomation = vi.fn()

    render(<ActivityPanel onOpenAutomation={onOpenAutomation} showAutomationSections={false} />)

    const section = (await screen.findByText('Needs attention')).closest('section') as HTMLElement
    expect(within(section).getByText('Log trades from email failed')).toBeTruthy()
    expect(within(section).getByText(/Lucius didn't answer in time/)).toBeTruthy()
    fireEvent.click(
      within(section).getByRole('button', {
        name: 'Open the failed run of Log trades from email',
      }),
    )
    expect(onOpenAutomation).toHaveBeenCalledWith('auto_1', 'run_1', 'automation-run-run_1')
  })

  it('counts review and failures together, and lists a failed run only once', async () => {
    const failedRun = {
      automation_id: 'auto_1',
      automation_name: 'Daily briefing',
      run_id: 'run_1',
      status: 'failed' as const,
      started_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
    }
    mockActivity({ active: [failedRun], scheduled: [], failed: [failedRun] })
    authFetch.mockResolvedValue(okJson({ transactions: [{ id: 'txn-1' }], total: 3 }))

    render(<ActivityPanel onOpenAutomation={vi.fn()} onOpenTransactionReview={vi.fn()} />)

    const section = (await screen.findByText('3 transactions to review')).closest(
      'section',
    ) as HTMLElement
    expect(within(section).getByText('2')).toBeTruthy()
    expect(within(section).getByText(/The run didn't finish/)).toBeTruthy()
    expect(screen.queryByText('Active')).toBeNull()
  })

  it('refreshes the backlog after a ledger change', async () => {
    mockActivity({ active: [], scheduled: [] })
    const { rerender } = render(
      <ActivityPanel onOpenAutomation={vi.fn()} onOpenTransactionReview={vi.fn()} />,
    )
    await waitFor(() => expect(authFetch).toHaveBeenCalledTimes(1))

    ledgerVersion = 1
    rerender(<ActivityPanel onOpenAutomation={vi.fn()} onOpenTransactionReview={vi.fn()} />)

    await waitFor(() => expect(authFetch).toHaveBeenCalledTimes(2))
  })

  it('renders nothing while loading (no data yet)', () => {
    mockActivity(undefined)
    const { container } = render(<ActivityPanel onOpenAutomation={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when both Active and Scheduled are empty', () => {
    mockActivity({ active: [], scheduled: [] })
    const { container } = render(<ActivityPanel onOpenAutomation={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders an Active section with a waiting row (Review shown) and a running row (no Review)', () => {
    mockActivity({
      active: [
        {
          automation_id: 'auto_1',
          automation_name: 'Needs Review',
          run_id: 'run_1',
          status: 'waiting',
          started_at: new Date().toISOString(),
        },
        {
          automation_id: 'auto_2',
          automation_name: 'Still Working',
          run_id: 'run_2',
          status: 'running',
          started_at: new Date().toISOString(),
        },
      ],
      scheduled: [],
    })
    render(<ActivityPanel onOpenAutomation={vi.fn()} />)

    expect(screen.getByText('Active')).toBeTruthy()
    expect(screen.getByText('Needs Review')).toBeTruthy()
    expect(screen.getByText(/Permissions needed/)).toBeTruthy()
    expect(screen.getByText('Still Working')).toBeTruthy()
    expect(screen.getByText(/^Running/)).toBeTruthy()

    // Review appears exactly once — only on the waiting row.
    expect(screen.getAllByRole('button', { name: 'Review' })).toHaveLength(1)
  })

  it('renders recently-completed and recently-failed rows with no Review button, using finished_at', () => {
    mockActivity({
      active: [
        {
          automation_id: 'auto_4',
          automation_name: 'Daily Trades Summary',
          run_id: 'run_4',
          status: 'completed',
          started_at: new Date(Date.now() - 600_000).toISOString(),
          finished_at: new Date(Date.now() - 120_000).toISOString(),
        },
        {
          automation_id: 'auto_5',
          automation_name: 'Weekly Fraud Checking',
          run_id: 'run_5',
          status: 'failed',
          started_at: new Date(Date.now() - 900_000).toISOString(),
          finished_at: new Date(Date.now() - 300_000).toISOString(),
        },
      ],
      scheduled: [],
    })
    render(<ActivityPanel onOpenAutomation={vi.fn()} />)

    expect(screen.getByText(/^Completed/)).toBeTruthy()
    expect(screen.getByText(/^Failed/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Review' })).toBeNull()
  })

  // Regression (owner report 2026-08-22): a run interrupted by a server restart is journaled
  // `cancelled`, not `failed` (ai/turns.py::shutdown()) — it was silently excluded from Active
  // even within the recency window, reading as "why isn't the run I just watched happen here?"
  it('renders a recently-cancelled row with no Review button, using finished_at', () => {
    mockActivity({
      active: [
        {
          automation_id: 'auto_6',
          automation_name: 'Log trade & transactions from emails',
          run_id: 'run_6',
          status: 'cancelled',
          started_at: new Date(Date.now() - 600_000).toISOString(),
          finished_at: new Date(Date.now() - 120_000).toISOString(),
        },
      ],
      scheduled: [],
    })
    render(<ActivityPanel onOpenAutomation={vi.fn()} />)

    expect(screen.getByText(/^Stopped/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Review' })).toBeNull()
  })

  it('does not reuse the turn indicator activity classes', () => {
    mockActivity({
      active: [
        {
          automation_id: 'auto_2',
          automation_name: 'Still Working',
          run_id: 'run_2',
          status: 'running',
          started_at: new Date().toISOString(),
        },
      ],
      scheduled: [],
    })
    const { container } = render(<ActivityPanel onOpenAutomation={vi.fn()} />)

    expect(container.querySelector('.lx-activity')).toBeNull()
    expect(container.querySelector('.lx-activity-dot')).toBeNull()
  })

  it('renders a Scheduled section with no action button', () => {
    const future = new Date(Date.now() + 3600_000).toISOString()
    mockActivity({
      active: [],
      scheduled: [
        { automation_id: 'auto_3', automation_name: 'Weekly Report', next_run_at: future },
      ],
    })
    render(<ActivityPanel onOpenAutomation={vi.fn()} />)

    expect(screen.getByText('Scheduled')).toBeTruthy()
    expect(screen.getByText('Weekly Report')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Review' })).toBeNull()
  })

  it('clicking the Review button navigates with the automation and run id, without also firing the row click', () => {
    const onOpenAutomation = vi.fn()
    mockActivity({
      active: [
        {
          automation_id: 'auto_1',
          automation_name: 'Needs Review',
          run_id: 'run_1',
          status: 'waiting',
          started_at: new Date().toISOString(),
        },
      ],
      scheduled: [],
    })
    render(<ActivityPanel onOpenAutomation={onOpenAutomation} />)

    fireEvent.click(screen.getByRole('button', { name: 'Review' }))

    expect(onOpenAutomation).toHaveBeenCalledTimes(1)
    expect(onOpenAutomation).toHaveBeenCalledWith('auto_1', 'run_1', undefined)
  })

  // Regression: opening an Active row from the landing screen used to leave the Lucius panel
  // on whatever empty draft conversation was already showing, instead of the run just opened
  // (owner report: tapping an Active row opened the right automation/run in the Automations
  // tab, but the panel beside it still showed a blank "New conversation"). The row's own
  // `session_id` — the run's conversation — is what ChatPanel.tsx's `openRun` uses to switch
  // the panel to it.
  it('passes the run session_id through so the caller can switch the panel to it', () => {
    const onOpenAutomation = vi.fn()
    mockActivity({
      active: [
        {
          automation_id: 'auto_2',
          automation_name: 'Still Working',
          run_id: 'run_2',
          session_id: 'session_abc',
          status: 'running',
          started_at: new Date().toISOString(),
        },
      ],
      scheduled: [],
    })
    render(<ActivityPanel onOpenAutomation={onOpenAutomation} />)

    fireEvent.click(screen.getByText('Still Working'))

    expect(onOpenAutomation).toHaveBeenCalledWith('auto_2', 'run_2', 'session_abc')
  })

  it('clicking a scheduled row navigates with just the automation id', () => {
    const onOpenAutomation = vi.fn()
    const future = new Date(Date.now() + 3600_000).toISOString()
    mockActivity({
      active: [],
      scheduled: [
        { automation_id: 'auto_3', automation_name: 'Weekly Report', next_run_at: future },
      ],
    })
    render(<ActivityPanel onOpenAutomation={onOpenAutomation} />)

    fireEvent.click(screen.getByText('Weekly Report'))

    expect(onOpenAutomation).toHaveBeenCalledWith('auto_3')
  })

  it('caps rows at 5 with a Show more / Show less toggle', () => {
    const active = Array.from({ length: 7 }, (_, i) => ({
      automation_id: `auto_${i}`,
      automation_name: `Automation ${i}`,
      run_id: `run_${i}`,
      status: 'running' as const,
      started_at: new Date().toISOString(),
    }))
    mockActivity({ active, scheduled: [] })
    render(<ActivityPanel onOpenAutomation={vi.fn()} />)

    expect(screen.getAllByText(/^Automation \d$/)).toHaveLength(5)
    fireEvent.click(screen.getByRole('button', { name: 'Show 2 more' }))
    expect(screen.getAllByText(/^Automation \d$/)).toHaveLength(7)
    fireEvent.click(screen.getByRole('button', { name: 'Show less' }))
    expect(screen.getAllByText(/^Automation \d$/)).toHaveLength(5)
  })
})
