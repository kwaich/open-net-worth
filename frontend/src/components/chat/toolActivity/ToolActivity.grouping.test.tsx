// @vitest-environment jsdom
//
// A thought does not end a run of activity.
//
// A turn that made five calls with a thought after the fourth rendered as TWO groups: a bar
// reading "Update 3 run tasks, add transactions · 4 steps" over four rows, then the thought,
// then a fifth row on its own. The second group had one row, and a one-row group by its own
// rules gets no bar — so it had no count and no fold, and collapsing the first group left the
// thought and that stray row still on screen (owner report, 2026-08-25, against Claude's own
// UI, which folds the whole run to a single line).

import { StrictMode } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ToolActivity } from './ToolActivity'
import type { ActionItem, ToolItem } from './cluster'

vi.mock('./useToolOwnerLogos', () => ({
  useToolOwnerLogos: () => () => ({}),
}))

afterEach(cleanup)

function call(id: string, label: string, verb = 'update'): ToolItem {
  return {
    type: 'tool',
    name: 'update_run_task',
    id,
    status: 'done',
    result: '{"ok":true}',
    display: {
      label,
      verb,
      noun: 'run tasks',
      owner_id: 'core',
      owner_name: '',
      detail: '',
    },
  }
}

/** The owner's own transcript: four calls, a thought, then a fifth call. */
function interrupted(): ActionItem[] {
  return [
    call('t1', 'Update run task'),
    call('t2', 'Update run task'),
    call('t3', 'Update run task'),
    { ...call('t4', 'Add transactions', 'create'), name: 'add_transactions' },
    { type: 'thinking', content: 'Most rows were already recorded from earlier runs.' },
    call('t5', 'Update run task'),
  ]
}

const bar = () => document.querySelector('.msg-toolact__summary--toggle') as HTMLButtonElement
const rail = () => document.querySelector('.msg-toolact__rail') as HTMLElement
const rows = () => document.querySelectorAll('.msg-toolact__row')

describe('a group spans the thought that interrupted it', () => {
  it('counts every call in the run, not just the ones before the thought', () => {
    render(<ToolActivity items={interrupted()} />)
    // Five calls happened. The bar used to say four.
    expect(screen.getByText('5 steps')).toBeTruthy()
  })

  it('does not count the thought as a step — it is what happened BETWEEN them', () => {
    render(
      <ToolActivity items={[call('t1', 'Update run task'), { type: 'thinking', content: 'x' }]} />,
    )
    expect(screen.getByText('1 step')).toBeTruthy()
  })

  it('folds the whole run — thought included — to a single line', () => {
    render(<ToolActivity items={interrupted()} />)
    // Nothing is streaming, so it arrives folded.
    expect(rail().hidden).toBe(true)
    expect(bar().getAttribute('aria-expanded')).toBe('false')
    // The thought is INSIDE the fold, which is the whole point: it used to be a sibling of
    // the group and survived every collapse.
    expect(rail().contains(screen.getByText(/Most rows were already recorded/))).toBe(true)
  })

  it('draws the thought in its true place — after the calls it followed', () => {
    render(<ToolActivity items={interrupted()} />)
    const labels = [...rows()].map((r) => r.textContent ?? '')
    expect(labels).toHaveLength(6) // 5 calls + the thought
    expect(labels[4]).toMatch(/Most rows were already recorded/)
    expect(labels[5]).toMatch(/Update run task/)
  })

  it('opens itself while the run is still happening, and folds when it is done', () => {
    const live: ActionItem[] = [
      { ...call('t1', 'Update run task'), status: 'executing', result: undefined },
    ]
    const { rerender } = render(<ToolActivity items={live} />)
    // A turn that runs for a minute behind one unchanging line reads as a hang.
    expect(rail().hidden).toBe(false)

    rerender(
      <ToolActivity items={[call('t1', 'Update run task'), call('t2', 'Update run task')]} />,
    )
    expect(rail().hidden).toBe(true)
  })

  it('stays open while the thought is being written', () => {
    render(
      <ToolActivity
        items={[
          call('t1', 'Update run task'),
          { type: 'thinking', content: 'Deciding…', streaming: true },
        ]}
      />,
    )
    expect(rail().hidden).toBe(false)
  })

  it('honours a click over both, permanently', () => {
    render(
      <StrictMode>
        <ToolActivity items={interrupted()} />
      </StrictMode>,
    )
    expect(rail().hidden).toBe(true)
    fireEvent.click(bar())
    expect(rail().hidden).toBe(false)
  })
})

describe('the bar always says what ran', () => {
  it('never folds down to a bare "1 step"', () => {
    // One call plus the thought that preceded it: two rows, so there is a bar — and the bar
    // is the only thing on screen once it folds. Suppressing its phrase as "a restatement of
    // the row below" left it reading just "1 step" above an answer (owner report,
    // 2026-08-25). It says the sentence now, with the count beside it.
    render(
      <ToolActivity
        items={[
          { type: 'thinking', content: 'The Flux transfer is the same movement.' },
          { ...call('t1', 'Update transactions'), name: 'update_transactions' },
        ]}
      />,
    )
    expect(bar().textContent).toContain('Update run tasks')
    expect(bar().textContent).toContain('1 step')
    expect(bar().textContent).not.toBe('1 step')
  })

  it('gives a lone call no bar at all — the row is its own sentence', () => {
    render(<ToolActivity items={[call('t1', 'Update run task')]} />)
    expect(document.querySelector('.msg-toolact__summary--toggle')).toBeNull()
    // ...and with no bar there is nothing to fold it behind.
    expect(rail().hidden).toBe(false)
  })
})

describe('the failure count is a count, not an alert', () => {
  const failing = (id: string): ToolItem => ({
    ...call(id, 'Get messages'),
    result: '{"error":"boom"}',
  })

  it('sits with the step count in the quiet trailing style, not inside the sentence', () => {
    render(<ToolActivity items={[call('t1', 'Get messages'), failing('t2')]} />)
    // The sentence is the loud half...
    const lead = bar().firstElementChild as HTMLElement
    expect(lead.textContent).toBe('Get run tasks')
    expect(lead.className).not.toContain('msg-toolact__count')
    // ...and both tallies ride the same quiet span, which is what "1 failed" used to escape.
    const tallies = bar().querySelector('.msg-toolact__count') as HTMLElement
    expect(tallies.textContent).toBe('2 steps • 1 failed')
  })

  it('counts a failed call as a step — it ran, it just did not work', () => {
    render(<ToolActivity items={[call('t1', 'Get messages'), failing('t2')]} />)
    // Two calls, two rows, two steps. A count that dropped the failure would disagree with
    // the rows underneath it.
    expect(bar().textContent).toContain('2 steps')
    expect(rows()).toHaveLength(2)
  })

  it('still states the failure when nothing succeeded to make a phrase from', () => {
    render(<ToolActivity items={[failing('t1')]} />)
    expect(bar().textContent).toContain('1 failed')
    expect(bar().textContent).toContain('1 step')
  })
})

describe("the discovery gate's own retry bounce is invisible to the user", () => {
  const gateBounce = (id: string, name: string): ActionItem => ({
    type: 'tool',
    name,
    id,
    status: 'done',
    result: JSON.stringify({
      error: `NOT EXECUTED — \`${name}\` did not run and returned no data.`,
      cause: 'you called it before its full definition was loaded; it is loaded now',
      next: `call \`${name}\` again to actually run it`,
      warning: 'this call produced NO results — do not summarise, report or act on any',
    }),
  })

  it('drops the bounced call entirely — it is not a step and not a failure', () => {
    render(
      <ToolActivity
        items={[gateBounce('t1', 'create_account'), call('t2', 'Create account', 'create')]}
      />,
    )
    // One real call happened. A lone call gets no bar at all.
    expect(document.querySelector('.msg-toolact__summary--toggle')).toBeNull()
    expect(rows()).toHaveLength(1)
  })

  it('never counts toward the failed tally, even when it is the only call this turn', () => {
    render(<ToolActivity items={[gateBounce('t1', 'create_account')]} />)
    expect(rows()).toHaveLength(0)
  })
})
