// @vitest-environment jsdom
//
// The cost panel showed only Tokens in / Tokens out. On a long run those are tiny (42 in,
// 6.2K out) while the real volume is prompt cache — 1.5M cache reads — so "$8.43" next to
// "42 in" read as a billing error (regression, 2026-08-11).
//
// Worse, that $8.43 is not a charge at all when the CLI runs on a Pro/Max login: it is a
// list price for usage the plan already covers, stamped cost_basis="plan_included". The
// stamp existed and was never displayed.

import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { AutomationRunCard } from './AutomationRunCard'
import type { AutomationRun } from '../../../api/automations'

afterEach(cleanup)

function makeRun(over: Partial<AutomationRun> = {}): AutomationRun {
  return {
    id: 'run_1',
    automation_id: 'task-1',
    session_id: 's1',
    status: 'completed',
    started_at: '2026-08-10T18:24:58Z',
    finished_at: '2026-08-10T18:26:56Z',
    input_tokens: 42,
    output_tokens: 6203,
    cache_creation_tokens: 91453,
    cache_read_tokens: 1512340,
    cost_usd: 8.4256113,
    stats: { model: 'sonnet' },
    ...over,
  } as AutomationRun
}

describe('AutomationRunCard cost panel', () => {
  it('shows the cache tokens that actually explain the cost', () => {
    render(<AutomationRunCard run={makeRun()} onClose={() => {}} />)
    expect(screen.getByText('Cache read')).toBeTruthy()
    expect(screen.getByText('Cache write')).toBeTruthy()
  })

  it('marks a plan-included figure as not a charge', () => {
    render(
      <AutomationRunCard
        run={makeRun({ stats: { model: 'sonnet', cost_basis: 'plan_included' } })}
        onClose={() => {}}
      />,
    )
    expect(screen.getByText('Total (plan-included)')).toBeTruthy()
    expect(screen.getByText(/not a charge/i)).toBeTruthy()
  })

  it('leaves a real API charge unqualified', () => {
    render(
      <AutomationRunCard
        run={makeRun({ stats: { model: 'sonnet', cost_basis: 'estimate' } })}
        onClose={() => {}}
      />,
    )
    expect(screen.getByText('Total')).toBeTruthy()
    expect(screen.queryByText(/not a charge/i)).toBeNull()
  })
})

describe('AutomationRunCard: a cancelled run explains itself', () => {
  // A cancelled run carries `error: null` on purpose — being stopped is not a failure, so
  // the red banner would be wrong. But the card rendered NOTHING else either: the
  // explanation went to `summary`, and no component read `summary` at all. The owner got a
  // "Stopped" chip, a checklist frozen at "Read current category list", and no reason
  // anywhere on the card (2026-08-21). Nineteen hours earlier a dev reload had cancelled it.
  it('shows why it stopped, as a notice rather than an error', () => {
    render(
      <AutomationRunCard
        run={makeRun({
          status: 'cancelled',
          error: null,
          summary: 'Did not finish — the server restarted while this run was working.',
        })}
        onClose={() => {}}
      />,
    )
    expect(screen.getByText(/the server restarted while this run was working/)).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('stays quiet when there is nothing to say', () => {
    render(
      <AutomationRunCard
        run={makeRun({ status: 'cancelled', error: null, summary: '' })}
        onClose={() => {}}
      />,
    )
    expect(screen.queryByRole('alert')).toBeNull()
    expect(document.querySelector('.a3-runcard .note')).toBeNull()
  })

  it('does not turn a completed run into a notice', () => {
    // `summary` is prose on a completed run — the conversation beside the card is where
    // that belongs. Only the cancelled case has no other channel.
    render(
      <AutomationRunCard run={makeRun({ summary: 'Logged 8 transactions.' })} onClose={() => {}} />,
    )
    expect(screen.queryByText('Logged 8 transactions.')).toBeNull()
  })
})
