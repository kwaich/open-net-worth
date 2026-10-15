// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { Automation, AutomationRun } from '../../../api/automations'
import { AutomationDetail } from './AutomationDetail'

const automation: Automation = {
  id: 'automation_a',
  name: 'Morning brief',
  description: 'What changed overnight.',
  instructions: 'Every weekday at 8am, give me a brief.',
  status: 'active',
  trigger: { kind: 'schedule', type: 'weekdays', time: '08:00', tzid: 'Asia/Singapore' },
  execution: { kind: 'lucius' },
  model: 'haiku',
  effort: 'low',
  run_count: 4,
  next_run_at: '2026-08-03T08:00:00+08:00',
  last_run_at: '2026-08-02T00:00:00Z',
  last_status: 'waiting',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-08-02T00:00:00Z',
  provenance: {},
  stats: { cost_this_month: 0.11 },
}

function run(
  status: AutomationRun['status'],
  id: string = status,
  sessionId: string | null = `session-${status}`,
): AutomationRun {
  return {
    id,
    automation_id: automation.id,
    status,
    session_id: sessionId,
    trigger: { kind: 'manual' },
    execution: { kind: 'lucius' },
    model: 'haiku',
    effort: 'low',
    started_at: '2026-08-02T08:00:00Z',
    finished_at: status === 'running' ? null : '2026-08-02T08:00:07Z',
    summary: `${status} summary`,
    error: status === 'failed' ? 'Connector unavailable' : null,
    steps: [{ label: 'Read accounts', done: status !== 'running' }],
    pending_tool_ids: status === 'waiting' ? ['tool_1'] : [],
    context: {},
    files: [],
    working_dir: null,
    input_tokens: status === 'running' ? null : 1200,
    output_tokens: status === 'running' ? null : 80,
    cache_creation_tokens: null,
    cache_read_tokens: null,
    cost_usd: status === 'running' ? null : 0.01,
    stats: {},
  }
}

afterEach(() => cleanup())

describe('AutomationDetail', () => {
  it('renders all durable run states and hands history selection to the global conversation owner', () => {
    const onSelectRun = vi.fn()
    const runs = [run('completed'), run('running'), run('waiting'), run('failed')]
    render(
      <AutomationDetail
        automation={automation}
        runs={runs}
        selectedRunId="waiting"
        onSelectRun={onSelectRun}
        onCloseRun={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={vi.fn()}
        onStopRun={vi.fn()}
        onStatusChange={vi.fn()}
        onRevoke={vi.fn()}
      />,
    )

    expect(document.querySelectorAll('.rd-dot.good')).toHaveLength(1)
    expect(document.querySelectorAll('.rd-dot.run')).toHaveLength(1)
    expect(document.querySelectorAll('.rd-dot.warn')).toHaveLength(1)
    expect(document.querySelectorAll('.rd-dot.bad')).toHaveLength(1)
    fireEvent.click(screen.getAllByRole('listitem')[0]!)
    expect(onSelectRun).toHaveBeenCalledWith(runs[0])
    expect(screen.queryByRole('button', { name: /^(approve|skip)$/i })).toBeNull()
  })

  it('renders an otherwise sparse durable conversation without inventing context or cost', () => {
    const sparse = {
      ...run('completed', 'sparse'),
      context: {},
      cost_usd: null,
      input_tokens: null,
      output_tokens: null,
    }
    render(
      <AutomationDetail
        automation={automation}
        runs={[sparse]}
        selectedRunId="sparse"
        onSelectRun={vi.fn()}
        onCloseRun={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={vi.fn()}
        onStopRun={vi.fn()}
        onStatusChange={vi.fn()}
        onRevoke={vi.fn()}
      />,
    )
    expect(screen.getByText('No context recorded.')).toBeTruthy()
    expect(screen.getAllByText('No cost data yet.').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /conversation/i })).toBeNull()
  })

  it('shows the approved empty-history guidance', () => {
    render(
      <AutomationDetail
        automation={automation}
        runs={[]}
        selectedRunId={null}
        onSelectRun={vi.fn()}
        onCloseRun={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={vi.fn()}
        onStopRun={vi.fn()}
        onStatusChange={vi.fn()}
        onRevoke={vi.fn()}
      />,
    )
    expect(screen.getByText(/Hasn’t run yet\. It first runs/)).toBeTruthy()
  })

  it('opens long instructions in the wide detail modal', () => {
    render(
      <AutomationDetail
        automation={{ ...automation, instructions: 'Review every relevant detail. '.repeat(16) }}
        runs={[]}
        selectedRunId={null}
        onSelectRun={vi.fn()}
        onCloseRun={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={vi.fn()}
        onStopRun={vi.fn()}
        onStatusChange={vi.fn()}
        onRevoke={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
    expect(document.querySelector('.ui-modal-card--xl')).toBeTruthy()
  })

  // Until 2026-08-06 a run could not be stopped from anywhere in the product: chat's Stop
  // 409'd an Automation-owned session on the reasoning that this tab owned the control, and
  // this tab had no such control. A scheduled run that looped could not be interrupted.
  it('offers Stop in place of Run now while a run is in flight, and names the run it stops', () => {
    const onStopRun = vi.fn()
    const onRunNow = vi.fn()
    const running = run('running')
    render(
      <AutomationDetail
        automation={automation}
        runs={[run('completed'), running]}
        selectedRunId={null}
        onSelectRun={vi.fn()}
        onCloseRun={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={onRunNow}
        onStopRun={onStopRun}
        onStatusChange={vi.fn()}
        onRevoke={vi.fn()}
      />,
    )

    expect(screen.queryByRole('button', { name: /run now/i })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /stop/i }))
    expect(onStopRun).toHaveBeenCalledWith(running)
    expect(onRunNow).not.toHaveBeenCalled()
  })

  it('offers Run now, not Stop, when nothing is in flight', () => {
    render(
      <AutomationDetail
        automation={automation}
        runs={[run('completed'), run('waiting')]}
        selectedRunId={null}
        onSelectRun={vi.fn()}
        onCloseRun={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={vi.fn()}
        onStopRun={vi.fn()}
        onStatusChange={vi.fn()}
        onRevoke={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: /run now/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^stop$/i })).toBeNull()
  })
})
