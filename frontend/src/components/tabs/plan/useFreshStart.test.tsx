// @vitest-environment jsdom
//
// "Start fresh from <month>…" — the kebab action end to end against a mocked authFetch: the
// dialog shows the REAL previewed Ready to Assign (never an estimate), confirming POSTs
// fresh-start, and Undo POSTs back the one-shot TOKEN it returned — never the amounts, which
// stay server-side (pre-merge review: a client-held snapshot made the undo route a bulk assign
// with the negative guard relaxed). Those two calls are the whole contract — assert the exact
// bodies, not just "something fired".
//
// "Resume earlier history" is the other, smaller write and stays a floor-only PUT.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
import { clearQueryCache } from '../../../state/queryCache'
import { useFreshStart } from './useFreshStart'
import { fBaseCents } from './planData'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>
const MONTH = '2026-09'
// The wire shape the endpoint actually returns — the promised figure plus its own arithmetic,
// cents-bearing (the dialog quotes the same precision the hero shows, so the promise reads as
// literally the after-state). These terms sum to `ready_to_assign_after` EXACTLY, as the
// endpoint guarantees (ADR 2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category —
// the SAME identity the RTA popover renders): 44773.55 − 0 − 207.43 − 0.
const PREVIEW = {
  month: MONTH,
  ready_to_assign_after: 44566.12,
  cash_now: 44773.55,
  held_in_envelopes: 0,
  held_outside_envelopes: 0,
  card_spend_not_covered: 207.43,
  baseline: 0,
}

/** What POST /api/planning/fresh-start hands back — a HANDLE, not the data. Undo posts the
 *  token back; the client holds no amounts and computes no inverse of its own. */
const HANDLE = { token: 'fsu_test_token', month: MONTH }

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}

/** Route the three calls this flow makes; `config.rollover_start` is what the GET reports. */
function wireApi(options: { rolloverStart?: string | null; previewFails?: boolean } = {}) {
  /** Every write this flow makes, in order: `[url, body]`. */
  const writes: Array<[string, Record<string, unknown>]> = []
  let rolloverStart = options.rolloverStart ?? null
  mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.startsWith('/api/planning/fresh-start-preview')) {
      if (options.previewFails) return { ok: false, json: async () => ({ detail: 'boom' }) }
      return okJson(PREVIEW)
    }
    if (url === '/api/planning/fresh-start' && init?.method === 'POST') {
      writes.push([url, JSON.parse(String(init.body))])
      rolloverStart = MONTH
      return okJson(HANDLE)
    }
    if (url === '/api/planning/fresh-start/undo' && init?.method === 'POST') {
      writes.push([url, JSON.parse(String(init.body))])
      rolloverStart = options.rolloverStart ?? null
      return { ok: true, status: 204, json: async () => null } as unknown as Response
    }
    if (url === '/api/planning/budget-config' && init?.method === 'PUT') {
      const body = JSON.parse(String(init.body))
      writes.push([url, body])
      rolloverStart = body.rollover_start
      return okJson({ rollover_start: rolloverStart, budget_start: null, pre_budget_baseline: 0 })
    }
    return okJson({ rollover_start: rolloverStart, budget_start: null, pre_budget_baseline: 0 })
  })
  return writes
}

function Harness({ toast, enabled }: { toast: (msg: string) => void; enabled?: boolean }) {
  const fresh = useFreshStart(MONTH, toast, enabled)
  return (
    <div>
      <button onClick={fresh.open}>{fresh.label}</button>
      {fresh.node}
    </div>
  )
}

beforeEach(() => {
  clearQueryCache()
  mockAuthFetch.mockReset()
})
afterEach(() => cleanup())

async function openDialog(toast = vi.fn()) {
  render(<Harness toast={toast} />)
  const trigger = await screen.findByRole('button', { name: /fresh from September 2026/ })
  fireEvent.click(trigger)
  return { toast }
}

describe('the kebab item', () => {
  it('names the current month and opens the dialog', async () => {
    wireApi()
    await openDialog()
    expect(await screen.findByText('Start fresh from September 2026?')).toBeTruthy()
  })

  it('reads past tense once this month IS the floor, and offers the inverse', async () => {
    wireApi({ rolloverStart: MONTH })
    render(<Harness toast={vi.fn()} />)
    const trigger = await screen.findByRole('button', {
      name: 'Started fresh from September 2026',
    })
    fireEvent.click(trigger)
    expect(await screen.findByText('Resume earlier history?')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Resume earlier history' })).toBeTruthy()
    // The inverse needs no figure — it restores what the book already had.
    expect(mockAuthFetch.mock.calls.some(([u]) => String(u).includes('fresh-start-preview'))).toBe(
      false,
    )
  })

  it('reads no config at all while another sub-tab is showing', async () => {
    wireApi()
    render(<Harness toast={vi.fn()} enabled={false} />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Start fresh from September 2026/ })).toBeTruthy(),
    )
    expect(mockAuthFetch).not.toHaveBeenCalled()
  })
})

describe('the confirm dialog', () => {
  it('renders the PREVIEWED Ready to Assign, and holds Start fresh until it arrives', async () => {
    wireApi()
    await openDialog()
    // Synchronously after the click the preview is still in flight: a skeleton stands in for
    // the figure and the decision cannot be taken yet.
    const confirm = screen.getByRole('button', { name: 'Start fresh' }) as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    expect(document.querySelector('.ui-skeleton')).toBeTruthy()

    // Cents, exactly as the hero renders it — the promised figure IS the after-state, to the cent.
    expect(await screen.findByText(fBaseCents(PREVIEW.ready_to_assign_after))).toBeTruthy()
    await waitFor(() => expect(confirm.disabled).toBe(false))
    expect(document.querySelector('.ui-skeleton')).toBeNull()
    expect(screen.getByText(/Ready to Assign becomes/)).toBeTruthy()
    // One closing paragraph, every consequence — including the disclosed side effect that the
    // carry floor is also the month-nav bound.
    expect(
      screen.getByText(
        /Envelopes start over from September — this month.s assignments are cleared\. This month.s spending still shows in its envelopes\. Earlier months hide until you resume history\. No transactions change/,
      ),
    ).toBeTruthy()
  })

  it('shows the promised figure its own arithmetic, ending at the cash frame it starts from', async () => {
    wireApi()
    await openDialog()
    expect(await screen.findByText('Cash on hand')).toBeTruthy()
    expect(screen.getByText(fBaseCents(PREVIEW.cash_now))).toBeTruthy()
    // The uncovered-card-spend term — always present, never a single-sentence estimate. Its
    // amount is UNSIGNED: the row's own "−" glyph column carries the sign, as in the popover.
    expect(screen.getByText('Card spending not covered this month')).toBeTruthy()
    expect(screen.getByText(fBaseCents(PREVIEW.card_spend_not_covered))).toBeTruthy()
    // What the envelopes hold is the identity's other backbone term and renders even at zero —
    // the same rule the popover follows, since both build their rows from `buildRtaLines`.
    expect(screen.getByText('Held in envelopes')).toBeTruthy()
    // The optional terms stay hidden at zero, never rows of "− S$0.00".
    expect(screen.queryByText('Baseline')).toBeNull()
    expect(screen.queryByText('Assigned in future months')).toBeNull()
    // ...and there is no this-month "assigned" row: the write clears the month's assignments,
    // so there is nothing assigned left for the promised figure to be net of.
    expect(screen.queryByText('Already assigned')).toBeNull()
  })

  // The ledger IS the popover's rows: same markup, same glyph column, same neutral colour for a
  // plain subtraction. The two used to differ on hideability, sign spelling AND colour.
  it('renders the ledger with the popover rows — one glyph column, no double minus', async () => {
    wireApi()
    await openDialog()
    await screen.findByText('Cash on hand')
    const rows = [...document.querySelectorAll('.pl-fresh-ledger .pl-rtapop-row')]
    expect(rows.map((r) => r.querySelector('.op')!.textContent)).toEqual(['+', '−', '−'])
    expect(rows.map((r) => r.querySelector('.val')!.textContent)).toEqual([
      fBaseCents(PREVIEW.cash_now),
      fBaseCents(0),
      fBaseCents(PREVIEW.card_spend_not_covered),
    ])
    expect(rows.filter((r) => r.classList.contains('warn'))).toHaveLength(1)
  })

  it('shows the inline error state and keeps Start fresh disabled when the preview fails', async () => {
    wireApi({ previewFails: true })
    await openDialog()
    expect(await screen.findByText("Couldn't work out the new Ready to Assign")).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: 'Start fresh' }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })
})

describe('confirm and undo', () => {
  // Undo names the server's OWN before-image — the client never derives an inverse and never
  // holds the amounts, which is what makes restoring N cleared assignments both reliable and
  // unforgeable.
  it('POSTs fresh-start, then Undo POSTs ONLY the token it returned', async () => {
    const writes = wireApi({ rolloverStart: '2026-06' })
    const toast = vi.fn()
    await openDialog(toast)
    const confirm = await screen.findByRole('button', { name: 'Start fresh' })
    await waitFor(() => expect((confirm as HTMLButtonElement).disabled).toBe(false))
    await act(async () => {
      fireEvent.click(confirm)
    })

    await waitFor(() => expect(writes).toEqual([['/api/planning/fresh-start', { ym: MONTH }]]))
    expect(await screen.findByText('Budget starts fresh from September 2026')).toBeTruthy()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    })
    await waitFor(() =>
      expect(writes).toEqual([
        ['/api/planning/fresh-start', { ym: MONTH }],
        ['/api/planning/fresh-start/undo', { token: HANDLE.token }],
      ]),
    )
    // Nothing money-shaped ever crosses the wire on the undo — the whole body is the token.
    expect(Object.keys(writes[1]?.[1] ?? {})).toEqual(['token'])
    expect(toast).toHaveBeenCalledWith('Undone')
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull()
  })

  it('a double-clicked Undo sends ONE write, not two', async () => {
    const writes = wireApi({ rolloverStart: '2026-06' })
    await openDialog()
    const confirm = await screen.findByRole('button', { name: 'Start fresh' })
    await waitFor(() => expect((confirm as HTMLButtonElement).disabled).toBe(false))
    await act(async () => {
      fireEvent.click(confirm)
    })
    await waitFor(() => expect(writes).toHaveLength(1))

    const undo = screen.getByRole('button', { name: 'Undo' })
    await act(async () => {
      fireEvent.click(undo)
      fireEvent.click(undo)
    })
    await waitFor(() => expect(writes).toHaveLength(2))
    expect(writes[1]).toEqual(['/api/planning/fresh-start/undo', { token: HANDLE.token }])
  })

  it('"Resume earlier history" clears the floor — and only the floor', async () => {
    const writes = wireApi({ rolloverStart: MONTH })
    render(<Harness toast={vi.fn()} />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Started fresh from September 2026' }),
    )
    expect(
      await screen.findByText(/Assignments cleared by the fresh start are not restored/),
    ).toBeTruthy()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Resume earlier history' }))
    })
    // The floor-only PUT, never the fresh-start POST — resuming restores no money.
    await waitFor(() =>
      expect(writes).toEqual([['/api/planning/budget-config', { rollover_start: null }]]),
    )
    expect(await screen.findByText('Carrying earlier history again')).toBeTruthy()
  })

  it('surfaces a refused write as a toast and arms no undo', async () => {
    wireApi()
    mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).startsWith('/api/planning/fresh-start-preview')) return okJson(PREVIEW)
      if (init?.method === 'POST')
        return { ok: false, json: async () => ({ detail: 'month out of range' }) }
      return okJson({ rollover_start: null, budget_start: null, pre_budget_baseline: 0 })
    })
    const toast = vi.fn()
    await openDialog(toast)
    const confirm = await screen.findByRole('button', { name: 'Start fresh' })
    await waitFor(() => expect((confirm as HTMLButtonElement).disabled).toBe(false))
    await act(async () => {
      fireEvent.click(confirm)
    })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('month out of range'))
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull()
  })
})
