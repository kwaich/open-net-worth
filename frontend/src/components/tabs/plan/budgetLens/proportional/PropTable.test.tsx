// @vitest-environment jsdom
//
// The live 50/30/20 screen: the book's envelopes in the book's Zero-Based groups (owner,
// 2026-09-26), each group with a share of income, each envelope with a monthly cap. Caps, shares
// and Excluded post the method's own operations; which group, the order and new groups are
// Zero-Based's own writes, so both screens always agree.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { clearQueryCache } from '../../../../../state/queryCache'
import { fMonth } from '../../../../../utils/format'
import { PropTable } from './PropTable'
import { currentYm } from '../period'
import type { PropEnvelope, PropMonth } from './propApi'

const authFetch = vi.fn<typeof fetch>()
vi.mock('../../../../../api/client', () => ({
  authFetch: (...args: Parameters<typeof fetch>) => authFetch(...args),
}))

// The fixtures are the 2026-09 book and the table opens on the CURRENT month, so pin "today".
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 8, 26))
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
  clearQueryCache()
  authFetch.mockReset()
})

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))
const hist = { last: 0, avg3: 0, last_by_day: 0, avg3_by_day: 0 }
const GROUP_NAMES: Record<string, string> = {
  grp_bills: 'Bills',
  grp_fun: 'Fun',
  grp_oneoff: 'One-offs',
}
const env = (
  id: string,
  label: string,
  groupId: string,
  activity: number,
  cap: number | null = null,
): PropEnvelope => ({
  id,
  label,
  color: '#3b86dd',
  group_id: groupId,
  group: GROUP_NAMES[groupId] ?? '',
  category_ids: ['cat_' + id],
  cap,
  activity,
  history: hist,
})
const group = (
  id: string,
  target: number | null,
  excluded = false,
): PropMonth['groups'][number] => ({
  id,
  name: GROUP_NAMES[id] ?? id,
  target,
  excluded,
  synced: false,
  activity: 0,
})

function month(over: Partial<PropMonth> = {}): PropMonth {
  return {
    month: '2026-09',
    status: 'current',
    day: 15,
    days_in_month: 30,
    configured: true,
    groups: [group('grp_bills', 50), group('grp_fun', 30), group('grp_oneoff', null, true)],
    savings: { amount: 7170, target: 20 },
    income: { amount: 10000, source: 'profile', profile: 10000, actual: 0 },
    income_categories: [],
    unassigned: { uncategorized: 0, no_envelope: 0 },
    envelopes: [
      env('env_rent', 'Rent', 'grp_bills', 2400, 2400),
      env('env_dining', 'Dining out', 'grp_fun', 350, 500),
      env('env_gifts', 'Gifts', 'grp_fun', 80),
      env('env_move', 'Moving', 'grp_oneoff', 0),
    ],
    ...over,
  }
}

function mockFetches(m: PropMonth = month()) {
  authFetch.mockImplementation((input, init) => {
    const url = String(input)
    if (url.startsWith('/api/planning/proportional/month')) return json(m)
    if (url === '/api/planning/groups')
      return json([
        { id: 'grp_bills', name: 'Bills' },
        { id: 'grp_commit', name: 'Commitments', synced: true },
      ])
    if (url.startsWith('/api/categories'))
      return json([{ id: 'cat_pets', label: 'Pets', kind: 'expense', color: '#e0a032' }])
    if (url === '/api/planning/envelopes') return json({ id: 'env_new' })
    if (url.includes('/delete-preview'))
      return json({ month: currentYm(), envelope_id: 'env_rent', available: 2400, delta_rta: 2400 })
    if ((init as RequestInit | undefined)?.method === 'DELETE')
      return json({
        ok: true,
        undo_token: 'edu_test',
        zeroed_assignments: { '2026-08': 120 },
        month: currentYm(),
        available: 2400,
        delta_rta: 2400,
      })
    return json({ ok: true })
  })
}

/** Every request to `path`, with its parsed JSON body. */
const sent = (path: string) =>
  authFetch.mock.calls
    .filter(([u]) => String(u) === path)
    .map(([, init]) => ({
      method: (init as RequestInit | undefined)?.method ?? 'GET',
      body: (init as RequestInit | undefined)?.body
        ? JSON.parse(String((init as RequestInit).body))
        : undefined,
    }))

const verbCalls = () =>
  authFetch.mock.calls
    .filter(([u]) => String(u) === '/api/planning/verb')
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)))

/** Forces useIsMobile() — budget.mobileCards.test.tsx's own helper: vitest.setup.ts installs
 *  matchMedia as a getter-only accessor, so it must be redefined, not assigned. */
function mockMobile(isMobile: boolean) {
  const fn = ((query: string) => ({
    matches: isMobile,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
}

const renderTable = () =>
  render(<PropTable toast={vi.fn()} onLucius={vi.fn()} onManageCategories={vi.fn()} />)

describe('PropTable', () => {
  it('shows the book’s own groups in their order — an Excluded one folded', async () => {
    mockFetches()
    renderTable()
    expect(await screen.findByText('Rent')).toBeTruthy()
    const names = [...document.querySelectorAll('.pt-grow .pl-gname-btn')].map((n) => n.textContent)
    expect(names).toEqual(['Bills', 'Fun', 'One-offs'])
    expect(screen.getByText('Dining out')).toBeTruthy()
    expect(screen.getByText('Gifts')).toBeTruthy()
    expect(screen.getByText(/target 50% ·/)).toBeTruthy()
    // Outside the split, so folded: its row isn't on screen until opened.
    expect(screen.getByText('Excluded')).toBeTruthy()
    expect(screen.queryByText('Moving')).toBeNull()
  })

  it('every row is a Lucius mention — drag it to Lucius or @ it, as on Zero-Based', async () => {
    mockFetches()
    renderTable()
    const row = (await screen.findByText('Rent')).closest('.pt-crow') as HTMLElement
    expect(row.getAttribute('data-entity-id')).toBe('envelope:env_rent')
    expect(row.getAttribute('data-entity-label')).toBe('Rent')
    expect(row.getAttribute('draggable')).toBe('true')
  })

  it('an envelope changes group from its editor’s Group dropdown — no Move button on the row', async () => {
    mockFetches()
    renderTable()
    const row = (await screen.findByText('Gifts')).closest('.pt-crow') as HTMLElement
    expect(within(row).queryByRole('button', { name: 'Move to another group' })).toBeNull()
    fireEvent.click(within(row).getByRole('button', { name: 'Edit Gifts' }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Group' }), {
      target: { value: 'Bills' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(sent('/api/planning/groups/move-category')).toContainEqual({
        method: 'POST',
        body: { category: 'env_gifts', group_id: 'grp_bills' },
      }),
    )
    expect(verbCalls()).toEqual([])
  })

  it('a group can leave the split — its menu posts set_group_excluded', async () => {
    mockFetches()
    renderTable()
    fireEvent.click(await screen.findByRole('button', { name: 'Fun group options' }))
    fireEvent.click(screen.getByRole('button', { name: 'Exclude from the split' }))
    await waitFor(() =>
      expect(verbCalls()).toContainEqual({
        engine_id: 'proportional',
        verb_id: 'set_group_excluded',
        params: { group_id: 'grp_fun', excluded: true },
      }),
    )
  })

  it('Add group creates a Zero-Based group — it shows on both screens', async () => {
    mockFetches()
    renderTable()
    fireEvent.click(await screen.findByText('Add group'))
    const input = screen.getByRole('textbox', { name: 'New group name' })
    fireEvent.change(input, { target: { value: 'Family' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() =>
      expect(sent('/api/planning/groups')).toContainEqual({
        method: 'POST',
        body: { name: 'Family' },
      }),
    )
  })

  it('editing a cap posts set_cap for this month onward', async () => {
    mockFetches()
    renderTable()
    fireEvent.click(await screen.findByRole('button', { name: 'Dining out monthly cap' }))
    const input = screen.getByRole('textbox', { name: 'Dining out monthly cap' })
    fireEvent.change(input, { target: { value: '650' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() =>
      expect(verbCalls()).toContainEqual({
        engine_id: 'proportional',
        verb_id: 'set_cap',
        params: { envelope_id: 'env_dining', month: '2026-09', amount: 650 },
      }),
    )
  })

  it('a cap with cents survives a click in and out untouched, and typed cents stay cents', async () => {
    mockFetches(month({ envelopes: [env('env_coffee', 'Coffee', 'grp_fun', 40, 83.33)] }))
    renderTable()
    fireEvent.click(await screen.findByRole('button', { name: 'Coffee monthly cap' }))
    fireEvent.blur(screen.getByRole('textbox', { name: 'Coffee monthly cap' }))
    // The regression: the digits-only parse read "83.33" as 8333 and saved it.
    expect(verbCalls()).toEqual([])

    fireEvent.click(await screen.findByRole('button', { name: 'Coffee monthly cap' }))
    const input = screen.getByRole('textbox', { name: 'Coffee monthly cap' })
    fireEvent.change(input, { target: { value: '12.50' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() =>
      expect(verbCalls()).toContainEqual({
        engine_id: 'proportional',
        verb_id: 'set_cap',
        params: { envelope_id: 'env_coffee', month: '2026-09', amount: 12.5 },
      }),
    )
  })

  it('a past month is history — its caps are read-only', async () => {
    mockFetches(month({ status: 'past' }))
    renderTable()
    await screen.findByText('Rent')
    expect(screen.queryByRole('button', { name: /monthly cap/ })).toBeNull()
  })

  it('Configure: a group’s new share is saved when the modal closes', async () => {
    mockFetches()
    renderTable()
    fireEvent.click(await screen.findByRole('button', { name: /Configure/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Raise Bills' }))
    fireEvent.click(screen.getByRole('button', { name: /close/i }))
    await waitFor(() =>
      expect(verbCalls()).toContainEqual({
        engine_id: 'proportional',
        verb_id: 'set_group_targets',
        params: { targets: { grp_bills: 55 } },
      }),
    )
  })
  it('a phone gets Zero-Based’s cards — every envelope keeps its icon, chip and three figures', async () => {
    const desktop = window.matchMedia
    mockMobile(true)
    try {
      mockFetches(
        month({
          envelopes: [
            env('env_rent', 'Rent', 'grp_bills', 2400, 2400),
            { ...env('env_pot', 'Holiday pot', 'grp_fun', 0), category_ids: [] },
          ],
        }),
      )
      renderTable()
      const rent = (await screen.findByText('Rent')).closest('.pt-mrow') as HTMLElement
      expect(rent.querySelector('.pl-cicon')).toBeTruthy()
      expect(rent.querySelector('.pl-envcats')?.textContent).toBe('1 category')
      for (const label of ['Cap', 'Activity', 'Left'])
        expect(within(rent).getByText(label)).toBeTruthy()
      // A zero-category envelope wears Zero-Based's own "0" hint, not a bespoke warning.
      const pot = screen.getByText('Holiday pot').closest('.pt-mrow') as HTMLElement
      expect(pot.querySelector('.pl-gcount--empty')).toBeTruthy()
      // The figures label themselves on a card — no table header.
      expect(document.querySelector('.pl-thead')).toBeNull()
    } finally {
      Object.defineProperty(window, 'matchMedia', {
        value: desktop,
        configurable: true,
        writable: true,
      })
    }
  })
  it('the editor only offers groups a spending envelope can move into', async () => {
    mockFetches()
    renderTable()
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Rent' }))
    const group = screen.getByRole('combobox', { name: 'Group' }) as HTMLSelectElement
    expect(group.value).toBe('Bills')
    const options = [...group.options].map((o) => o.value)
    expect(options).toContain('Bills')
    expect(options).not.toContain('Commitments')
    expect(options).not.toContain('Income')
  })

  it('the editor adds categories with the transaction picker — search, pick, or create', async () => {
    mockFetches()
    renderTable()
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Rent' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit envelope' })
    // One control, not a select plus a separate "create" box.
    expect(within(dialog).queryByPlaceholderText('or create a new category')).toBeNull()
    fireEvent.click(within(dialog).getByText('Add a category…'))
    fireEvent.mouseDown(await within(dialog).findByText('Pets'))
    expect(within(dialog).getByRole('button', { name: 'Remove Pets' })).toBeTruthy()
    // A name nothing matches offers to create it — on Save, as an expense category.
    fireEvent.click(within(dialog).getByText('Add a category…'))
    fireEvent.change(within(dialog).getByPlaceholderText('Search categories…'), {
      target: { value: 'Pet insurance' },
    })
    fireEvent.mouseDown(within(dialog).getByText(/New category/))
    expect(within(dialog).getByRole('button', { name: 'Remove Pet insurance' })).toBeTruthy()
  })

  it('the picker offers no nameless "New category" before anything is typed', async () => {
    mockFetches()
    renderTable()
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Rent' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit envelope' })
    fireEvent.click(within(dialog).getByText('Add a category…'))
    expect(await within(dialog).findByText('Pets')).toBeTruthy()
    expect(within(dialog).queryByText(/New category/)).toBeNull()
  })

  it('a cap the server refuses comes back off the screen — the month is re-read', async () => {
    mockFetches()
    const monthReads = () =>
      authFetch.mock.calls.filter(([u]) => String(u).startsWith('/api/planning/proportional/month'))
        .length
    renderTable()
    fireEvent.click(await screen.findByRole('button', { name: 'Dining out monthly cap' }))
    const reads = monthReads()
    authFetch.mockImplementationOnce(() =>
      Promise.resolve(new Response(JSON.stringify({ detail: 'nope' }), { status: 400 })),
    )
    const input = screen.getByRole('textbox', { name: 'Dining out monthly cap' })
    fireEvent.change(input, { target: { value: '650' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(monthReads()).toBeGreaterThan(reads))
  })
  it('Delete asks first in Zero-Based’s dialog, then offers Undo — which replays the undo_token whole', async () => {
    mockFetches()
    const toast = vi.fn()
    render(<PropTable toast={toast} onLucius={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Rent' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete envelope…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete envelope' })
    expect(within(dialog).getByText(/cap stops counting toward Bills/)).toBeTruthy()
    // The TRUE realized figure (envelope_delete_preview), fetched for THIS envelope/month —
    // not the old, undercounting `available` the dialog used to hardcode to 0.
    expect(await within(dialog).findByText(/returns to Ready to Assign/)).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete envelope' }))
    await waitFor(() =>
      expect(sent(`/api/planning/envelopes/env_rent?ym=${currentYm()}`)).toContainEqual({
        method: 'DELETE',
        body: undefined,
      }),
    )
    // The result (after) echoes the SAME server-realized figure the confirm dialog showed.
    expect(toast).toHaveBeenCalledWith(expect.stringContaining('Envelope “Rent” deleted — '))
    expect(toast).toHaveBeenCalledWith(expect.stringContaining('returned to Ready to Assign'))
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }))
    await waitFor(() =>
      expect(sent('/api/planning/envelopes/undo-delete')).toContainEqual({
        method: 'POST',
        body: { token: 'edu_test' },
      }),
    )
    // The client never replays zeroed_assignments or re-archives itself any more — the
    // server's undo route does the whole thing atomically.
    expect(sent('/api/planning/apply')).toEqual([])
    expect(sent('/api/planning/envelopes/env_rent/restore')).toEqual([])
  })

  it('removing a category from the row’s chip asks first', async () => {
    mockFetches()
    renderTable()
    const row = (await screen.findByText('Rent')).closest('.pt-crow') as HTMLElement
    fireEvent.click(row.querySelector('.pl-envcats') as HTMLElement)
    fireEvent.click(await screen.findByTitle('Remove cat_env_rent from this envelope'))
    const dialog = await screen.findByRole('dialog', { name: 'Remove category from envelope' })
    expect(sent('/api/planning/envelopes/env_rent/split')).toEqual([])
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove from envelope' }))
    await waitFor(() =>
      expect(sent('/api/planning/envelopes/env_rent/split')).toEqual([
        { method: 'POST', body: { category_id: 'cat_env_rent' } },
      ]),
    )
  })

  it('Add envelope is Zero-Based’s New envelope form — it links the picked categories', async () => {
    mockFetches()
    renderTable()
    await screen.findByText('Rent')
    const add = document.querySelector('.pt-add') as HTMLElement
    fireEvent.click(within(add).getByRole('button', { name: /Add envelope/ }))
    const dialog = await screen.findByRole('dialog', { name: 'New envelope' })
    fireEvent.click(within(dialog).getByText('Pets'))
    fireEvent.click(within(dialog).getByRole('button', { name: /Create/ }))
    await waitFor(() =>
      expect(sent('/api/planning/envelopes/env_new/add-category')).toEqual([
        { method: 'POST', body: { category_id: 'cat_pets' } },
      ]),
    )
    expect(sent('/api/planning/envelopes')[0]?.body).toEqual({ name: 'Pets', color: '#e0a032' })
  })

  it('Merge picks its target in Zero-Based’s dialog and carries the cap over', async () => {
    mockFetches()
    renderTable()
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Rent' }))
    fireEvent.click(screen.getByRole('button', { name: 'Merge into another envelope…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Merge envelope' })
    fireEvent.change(within(dialog).getByRole('combobox'), { target: { value: 'env_dining' } })
    expect(within(dialog).getByText(/cap adds to Dining out/)).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Merge envelopes' }))
    await waitFor(() =>
      expect(verbCalls()).toContainEqual({
        engine_id: 'proportional',
        verb_id: 'set_cap',
        params: { envelope_id: 'env_dining', month: '2026-09', amount: 2900 },
      }),
    )
    expect(sent('/api/planning/envelopes/merge')[0]?.body).toEqual({
      source_envelope_id: 'env_rent',
      target_envelope_id: 'env_dining',
    })
  })

  it('stepping months keeps the month on screen, dimmed, until the next one arrives', async () => {
    mockFetches()
    renderTable()
    await screen.findByText('Rent')
    const answered = authFetch.getMockImplementation()!
    authFetch.mockImplementation((input, init) =>
      String(input).includes('2026-10') ? new Promise(() => {}) : answered(input, init),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Next month' }))
    expect(await screen.findByText(fMonth('2026-10'))).toBeTruthy()
    expect(screen.getByText('Rent')).toBeTruthy()
    expect(document.querySelector('.pt-body.is-switching')).toBeTruthy()
  })
  it('a click anywhere on a row opens its editor — its own controls keep their own click', async () => {
    mockFetches()
    renderTable()
    const row = (await screen.findByText('Rent')).closest('.pt-crow') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Rent monthly cap' }))
    expect(screen.queryByRole('dialog', { name: 'Edit envelope' })).toBeNull()
    fireEvent.click(row.querySelector('.pl-cicon') as HTMLElement)
    expect(await screen.findByRole('dialog', { name: 'Edit envelope' })).toBeTruthy()
  })

  it('an envelope with no cap and no spending reads "—" in Cap and Left, not a S$0.00 limit', async () => {
    mockFetches(month({ envelopes: [env('env_tax', 'Tax', 'grp_bills', 0)] }))
    renderTable()
    const row = (await screen.findByText('Tax')).closest('.pt-crow') as HTMLElement
    const cells = row.querySelectorAll('.pl-c-num')
    expect(cells[0]?.textContent).toBe('—')
    expect(cells[2]?.textContent).toBe('—')
  })

  it('an envelope with no cap but spending shows that spend in the warning tint', async () => {
    mockFetches(month({ envelopes: [env('env_tax', 'Tax', 'grp_bills', 900)] }))
    renderTable()
    const row = (await screen.findByText('Tax')).closest('.pt-crow') as HTMLElement
    const cells = row.querySelectorAll('.pl-c-num')
    expect(cells[0]?.textContent).toBe('—')
    const left = cells[2]?.querySelector('.pl-avail') as HTMLElement
    expect(left.className).toContain('under')
    expect(left.textContent).toContain('900.00')
  })
})

describe('PropTable — the group header is Zero-Based’s', () => {
  const headOf = (name: string) =>
    [...document.querySelectorAll('.pt-grow')].find(
      (el) => el.querySelector('.pl-gname-btn')?.textContent === name,
    ) as HTMLElement

  it('a group’s + opens New envelope in that group — it lands there', async () => {
    mockFetches()
    renderTable()
    await screen.findByText('Rent')
    const bills = headOf('Bills')
    fireEvent.click(within(bills).getByRole('button', { name: 'Add envelope' }))
    const dialog = await screen.findByRole('dialog', { name: 'New envelope' })
    fireEvent.click(within(dialog).getByText('Pets'))
    fireEvent.click(within(dialog).getByRole('button', { name: /Create/ }))
    await waitFor(() =>
      expect(sent('/api/planning/envelopes')[0]?.body).toEqual({
        name: 'Pets',
        color: '#e0a032',
        group_id: 'grp_bills',
      }),
    )
  })

  it('a group dragged by its grip is Zero-Based’s reorder — every group sent, the rest in place', async () => {
    mockFetches()
    const base = authFetch.getMockImplementation()!
    authFetch.mockImplementation((input, init) =>
      String(input) === '/api/planning/groups'
        ? json([
            { id: 'grp_commit', name: 'Commitments', synced: true },
            { id: 'grp_bills', name: 'Bills' },
            { id: 'grp_fun', name: 'Fun' },
            { id: 'grp_oneoff', name: 'One-offs' },
          ])
        : base(input, init),
    )
    renderTable()
    const dt = { effectAllowed: '', dropEffect: '', setData: () => {}, setDragImage: () => {} }
    const head = headOf
    await screen.findByText('Rent')
    await waitFor(() => expect(sent('/api/planning/groups').length).toBeGreaterThan(0))
    fireEvent.dragStart(screen.getByRole('button', { name: 'Drag group Fun' }), {
      dataTransfer: dt,
    })
    fireEvent.dragOver(head('Bills'), { dataTransfer: dt })
    expect(head('Bills').className).toContain('grow-over')
    fireEvent.drop(head('Bills'), { dataTransfer: dt })
    // At once, before the save round-trips (this mock month never changes, so a refetch would put
    // the old order back).
    const names = [...document.querySelectorAll('.pt-grow .pl-gname-btn')].map((n) => n.textContent)
    expect(names).toEqual(['Fun', 'Bills', 'One-offs'])
    await waitFor(() =>
      expect(sent('/api/planning/groups/reorder')).toEqual([
        {
          method: 'POST',
          body: { order: ['grp_commit', 'grp_fun', 'grp_bills', 'grp_oneoff'] },
        },
      ]),
    )
  })
})

describe('PropTable — drag to reorder', () => {
  // jsdom has no layout: every row is 0px tall, so a drag over a row reads as its lower half and
  // the dragged row lands BELOW it.
  const dt = { effectAllowed: '', dropEffect: '', setData: () => {}, setDragImage: () => {} }
  const rowOf = (label: string) => screen.getByText(label).closest('.pt-crow') as HTMLElement
  const names = () => [...document.querySelectorAll('.pt-crow .pt-ename')].map((n) => n.textContent)

  it('a row dropped in its own group lands there — Zero-Based’s reorder saves the group', async () => {
    mockFetches(
      month({
        envelopes: [
          env('env_rent', 'Rent', 'grp_bills', 2400, 2400),
          env('env_tax', 'Tax', 'grp_bills', 900),
          env('env_food', 'Groceries', 'grp_bills', 300, 400),
        ],
      }),
    )
    renderTable()
    await screen.findByText('Rent')
    fireEvent.dragStart(rowOf('Rent'), { dataTransfer: dt })
    fireEvent.dragOver(rowOf('Groceries'), { dataTransfer: dt })
    expect(rowOf('Groceries').className).toContain('over-bottom')
    fireEvent.drop(rowOf('Groceries'), { dataTransfer: dt })
    expect(names()).toEqual(['Tax', 'Groceries', 'Rent'])
    await waitFor(() =>
      expect(sent('/api/planning/groups/grp_bills/reorder-categories')).toContainEqual({
        method: 'POST',
        body: { categories: ['env_tax', 'env_food', 'env_rent'] },
      }),
    )
    // One order for both methods: nothing of the 50/30/20 engine's own is written.
    expect(verbCalls()).toEqual([])
  })

  it('a row dropped in another group moves there, where it was dropped — Zero-Based’s move', async () => {
    mockFetches(
      month({
        envelopes: [
          env('env_rent', 'Rent', 'grp_bills', 2400, 2400),
          env('env_dining', 'Dining out', 'grp_fun', 350, 500),
          env('env_coffee', 'Coffee', 'grp_fun', 40),
        ],
      }),
    )
    renderTable()
    await screen.findByText('Rent')
    fireEvent.dragStart(rowOf('Rent'), { dataTransfer: dt })
    fireEvent.dragOver(rowOf('Dining out'), { dataTransfer: dt })
    fireEvent.drop(rowOf('Dining out'), { dataTransfer: dt })
    await waitFor(() =>
      expect(sent('/api/planning/groups/move-category')).toContainEqual({
        method: 'POST',
        body: { category: 'env_rent', group_id: 'grp_fun', index: 1 },
      }),
    )
  })

  it('an empty group says nothing until a row is being dragged — then it is the target', async () => {
    mockFetches(month({ envelopes: [env('env_rent', 'Rent', 'grp_bills', 2400, 2400)] }))
    renderTable()
    await screen.findByText('Rent')
    expect(screen.queryByText('Empty')).toBeNull()
    expect(screen.queryByText('Drop here')).toBeNull()
    fireEvent.dragStart(rowOf('Rent'), { dataTransfer: dt })
    expect(screen.getAllByText('Drop here').length).toBeGreaterThan(0)
  })
})

describe('PropTable — spending the split leaves out, and spending no envelope owns', () => {
  const dt = { effectAllowed: '', dropEffect: '', setData: () => {}, setDragImage: () => {} }
  const sectionOf = (title: string) =>
    [...document.querySelectorAll('.pt-group')].find(
      (el) => el.querySelector('.pt-outhead .pl-gname-btn')?.textContent === title,
    ) as HTMLElement | undefined

  it('an envelope excluded on its own sits in Excluded, not its group — its group no longer counts it', async () => {
    mockFetches(
      month({
        envelopes: [
          env('env_rent', 'Rent', 'grp_bills', 2400, 2400),
          { ...env('env_loan', 'Proxy loan', 'grp_bills', 3868), excluded: true },
        ],
      }),
    )
    renderTable()
    await screen.findByText('Rent')
    const excluded = sectionOf('Excluded')!
    expect(within(excluded).getByText('Proxy loan')).toBeTruthy()
    const bills = [...document.querySelectorAll('.pt-group')].find(
      (el) => el.querySelector('.pl-gname-btn')?.textContent === 'Bills',
    ) as HTMLElement
    expect(within(bills).queryByText('Proxy loan')).toBeNull()
  })

  it('a row dragged into Excluded posts set_envelope_excluded', async () => {
    mockFetches(
      month({
        envelopes: [
          env('env_rent', 'Rent', 'grp_bills', 2400, 2400),
          { ...env('env_loan', 'Proxy loan', 'grp_bills', 3868), excluded: true },
          env('env_gifts', 'Gifts', 'grp_fun', 80),
        ],
      }),
    )
    renderTable()
    await screen.findByText('Rent')
    const gifts = screen.getByText('Gifts').closest('.pt-crow') as HTMLElement
    fireEvent.dragStart(gifts, { dataTransfer: dt })
    fireEvent.dragOver(sectionOf('Excluded')!, { dataTransfer: dt })
    fireEvent.drop(sectionOf('Excluded')!, { dataTransfer: dt })
    await waitFor(() =>
      expect(verbCalls()).toContainEqual({
        engine_id: 'proportional',
        verb_id: 'set_envelope_excluded',
        params: { envelope_id: 'env_gifts', excluded: true },
      }),
    )
  })

  it('spending no envelope owns has rows — Uncategorized, then each category', async () => {
    mockFetches(
      month({
        unassigned: {
          uncategorized: 45,
          no_envelope: 120,
          categories: [{ id: 'cat_pets', label: 'Pets', color: '#e0a032', activity: 120 }],
        },
      }),
    )
    renderTable()
    await screen.findByText('Rent')
    const outside = sectionOf('Not in an envelope')!
    expect(within(outside).getByText('Uncategorized')).toBeTruthy()
    expect(within(outside).getByText('Pets')).toBeTruthy()
    expect(within(outside).getByText('not planned, so it reduces your savings')).toBeTruthy()
  })

  it('unticking "Count in the split" in the editor takes that envelope out', async () => {
    mockFetches()
    renderTable()
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Gifts' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Count in the split' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(verbCalls()).toContainEqual({
        engine_id: 'proportional',
        verb_id: 'set_envelope_excluded',
        params: { envelope_id: 'env_gifts', excluded: true },
      }),
    )
  })
})
