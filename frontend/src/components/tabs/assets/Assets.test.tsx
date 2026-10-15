// @vitest-environment jsdom
//
// The Assets shell against a mocked authFetch (the real useAssets + api.ts): a category chip
// starts the wizard at step 2, a created asset closes the wizard, is confirmed and opens its
// page at once — and a background refetch that fails while a dialog is saving never unmounts
// that dialog (review finding: it used to, and a retry then created a second loan). Synthetic
// data only; dialogs render through a Radix portal, so queries use `screen`.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetGuidedForTests } from '../../intro/tourTarget'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { AssetCategory, AssetsPayload, OwnedAsset } from '../../../types/assets'
import { setBaseCurrency } from '../../../utils/format'
import { deleteCachedQueryData } from '../../../state/queryCache'

const { authFetch } = vi.hoisted(() => ({ authFetch: vi.fn() }))
vi.mock('../../../api/client', () => ({ authFetch }))

// The asset page mounts the value chart; jsdom can't rasterize a canvas.
vi.mock('chart.js', () => {
  class FakeChart {
    static register = vi.fn()
    data: { datasets: unknown[] } = { datasets: [] }
    options = {}
    update = vi.fn()
    destroy = vi.fn()
  }
  return {
    Chart: FakeChart,
    LineController: {},
    LineElement: {},
    PointElement: {},
    LinearScale: {},
    Tooltip: {},
    Filler: {},
  }
})
vi.mock('../../../theme', () => ({
  useTheme: () => ({ preference: 'dark', resolved: 'dark', setPreference: vi.fn() }),
}))

import { Assets } from './Assets'

setBaseCurrency('SGD')

const CATS: AssetCategory[] = [
  {
    key: 'vehicle',
    label: 'Vehicle',
    plural: 'Vehicles',
    color: 'var(--cat-vehicle)',
    custom: false,
  },
  {
    key: 'watch',
    label: 'Watch & jewellery',
    plural: 'Watches & jewellery',
    color: 'var(--cat-watch)',
    custom: false,
  },
]

function asset(overrides: Partial<OwnedAsset>): OwnedAsset {
  return {
    id: 'oa_car0000001',
    name: 'Test Car',
    category: 'vehicle',
    description: '',
    photo_ref: null,
    facts: [],
    notes: '',
    cost: null,
    cost_date: null,
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: 52000,
    valued_on: '2026-09-26',
    share_value: 52000,
    marks: [
      {
        id: 'mk_1',
        kind: 'valuation',
        date: '2026-09-26',
        value: 52000,
        note: '',
        by: 'you',
        created_at: '2026-09-26T00:00:00Z',
        edited_at: null,
      },
    ],
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2026-09-26T00:00:00Z',
    updated_at: '2026-09-26T00:00:00Z',
    ...overrides,
  }
}

const CAR = asset({})
const WATCH = asset({ id: 'oa_watch00001', name: 'Test Watch', category: 'watch', value: 14800 })
// Four more of one category: a stack on All, its own cards once opened.
const watches = [1, 2, 3, 4].map((i) => ({
  ...WATCH,
  id: `oa_watch0000${i}`,
  name: `Watch ${i}`,
  value: i * 1000,
  base_value: i * 1000,
  base_share_value: i * 1000,
}))

function payload(assets: OwnedAsset[]): AssetsPayload {
  const cash = {
    value: 0,
    sub: '',
    yield_label: null,
    gain: null,
    gain_pct: null,
    as_of: 'today',
    count_label: '',
    rows: null,
  }
  return {
    assets,
    categories: CATS,
    loans: [],
    cash_accounts: [],
    base_currency: 'SGD',
    summary: {
      own: 0,
      owe: 0,
      net_worth: 0,
      unrealized: 0,
      classes: {
        cash,
        investments: {
          value: 0,
          sub: '',
          gain: 0,
          gain_pct: null,
          as_of: 'today',
          count_label: '',
          rows: null,
        },
        retirement: null,
        receivable: null,
        debt: null,
      },
      debt: null,
      composition: { total: 0, rows: [] },
    },
  }
}

function ok(data: unknown): Response {
  return { ok: true, status: 200, json: async () => data } as Response
}

function refused(status: number, body: unknown): Response {
  return { ok: false, status, json: async () => body } as Response
}

/** Answers "METHOD path" from its own queue — a pending promise holds an answer back. */
function serve(routes: Record<string, (Response | Promise<Response>)[]>) {
  authFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${path}`
    const next = routes[key]?.shift()
    if (!next) throw new Error(`unexpected ${key}`)
    return next
  })
}

const calls = (key: string) =>
  authFetch.mock.calls.filter(
    ([path, init]) => `${(init as RequestInit | undefined)?.method ?? 'GET'} ${path}` === key,
  ).length

const subtitle = () => document.querySelector('.ui-modal__subtitle')?.textContent
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole('button', { name }))
const type = (label: string | RegExp, text: string) =>
  fireEvent.change(screen.getByRole('textbox', { name: label }), { target: { value: text } })

function typeMoney(label: string | RegExp, text: string) {
  const input = screen.getByRole('textbox', { name: label })
  fireEvent.focus(input)
  fireEvent.change(input, { target: { value: text } })
  fireEvent.blur(input)
}

function renderShell() {
  render(<Assets onAskLucius={vi.fn()} onNavigate={vi.fn()} />)
}

// Each test renders the whole tab — register, wizard, refetches: about a second alone, several
// times that on a loaded machine, where the default 5s timeout fails for no real reason.
const SHELL_TIMEOUT = 20_000

// A block body: an arrow returning the spy would hand Vitest a teardown that calls it.
beforeEach(() => {
  authFetch.mockReset()
  deleteCachedQueryData('/api/assets')
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
})
afterEach(() => {
  cleanup()
  resetGuidedForTests() // every test is a fresh visit
})

it('previews what the page holds instead of zero-value cards on an empty book', async () => {
  serve({ 'GET /api/assets': [ok(payload([]))] })
  renderShell()
  expect(await screen.findByRole('button', { name: 'Add bank account' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Add credit card' })).toBeTruthy()
  expect(screen.queryByText('Asset Composition')).toBeNull()
  expect(screen.queryByText('Everything you own')).toBeNull()
  expect(screen.getAllByRole('button', { name: 'Add asset' })).toHaveLength(1)
})

it('an account tile opens the add-account form on its type', async () => {
  serve({ 'GET /api/assets': [ok(payload([]))] })
  const onNavigate = vi.fn()
  render(<Assets onAskLucius={vi.fn()} onNavigate={onNavigate} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Add retirement account' }))
  expect(onNavigate).toHaveBeenCalledWith('accounts', { add: 'retirement' })
  fireEvent.click(screen.getByRole('button', { name: 'Add credit card' }))
  expect(onNavigate).toHaveBeenCalledWith('accounts', { add: 'credit' })
})

describe('Assets — first-visit intro', () => {
  beforeEach(() => deleteCachedQueryData('/api/profile'))

  const unseen = () => ok({ seen_intros: [] })

  it('introduces an empty page once, and Set up myself starts the tour', async () => {
    serve({
      'GET /api/assets': [ok(payload([]))],
      'GET /api/profile': [unseen(), unseen()],
      'PUT /api/profile': [ok({ ok: true, profile: { seen_intros: ['assets'] } })],
    })
    renderShell()
    expect(
      await screen.findByRole('dialog', { name: 'Your whole net worth, in one place' }),
    ).toBeTruthy()
    click('Set up myself')
    expect(await screen.findByText('Step 1 of 3')).toBeTruthy()
    await waitFor(() => expect(calls('PUT /api/profile')).toBe(1))
    const put = authFetch.mock.calls.find(([, i]) => (i as RequestInit)?.method === 'PUT')!
    expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ seen_intros: ['assets'] })
  })

  it('Set up with Lucius hands the setup to Lucius', async () => {
    serve({
      'GET /api/assets': [ok(payload([]))],
      'GET /api/profile': [unseen(), unseen()],
      'PUT /api/profile': [ok({ ok: true, profile: { seen_intros: ['assets'] } })],
    })
    const onAskLucius = vi.fn()
    render(<Assets onAskLucius={onAskLucius} onNavigate={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Set up with Lucius' }))
    expect(onAskLucius).toHaveBeenCalledWith(expect.stringContaining('Assets page'))
    expect(screen.queryByText('Step 1 of 3')).toBeNull()
  })

  it('on a book that already holds money, explains the page instead of setting it up', async () => {
    const funded = payload([])
    funded.summary.own = 5000
    funded.summary.net_worth = 5000
    serve({
      'GET /api/assets': [ok(funded)],
      'GET /api/profile': [unseen(), unseen()],
      'PUT /api/profile': [ok({ ok: true, profile: { seen_intros: ['assets'] } })],
    })
    const onAskLucius = vi.fn()
    render(<Assets onAskLucius={onAskLucius} onNavigate={vi.fn()} />)
    expect(await screen.findByRole('button', { name: 'Got it' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Set up myself' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Walk me through it' }))
    expect(onAskLucius).toHaveBeenCalledWith(expect.stringContaining('my Assets screen'))
  })

  it('stays away once seen', async () => {
    serve({
      'GET /api/assets': [ok(payload([]))],
      'GET /api/profile': [ok({ seen_intros: ['assets'] })],
    })
    renderShell()
    await screen.findByRole('button', { name: 'Add bank account' })
    expect(screen.queryByRole('dialog', { name: 'Your whole net worth, in one place' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'How it works' })).toBeNull()
  })
})

it(
  'a placeholder tile opens the wizard on its own category',
  async () => {
    serve({ 'GET /api/assets': [ok(payload([]))] })
    renderShell()
    fireEvent.click(await screen.findByRole('button', { name: 'Add vehicle' }))
    expect(await screen.findByRole('textbox', { name: /^Name/ })).toBeTruthy()
  },
  SHELL_TIMEOUT,
)

describe('Assets — a failed refetch while saving', () => {
  it(
    'keeps the wizard and its created loan — a retry creates no second loan',
    async () => {
      serve({
        'GET /api/assets': [ok(payload([])), refused(503, { detail: 'Busy.' }), ok(payload([CAR]))],
        'POST /api/accounts': [ok({ id: 'acc_newloan1', name: 'Test Car Loan', balance: -38000 })],
        'POST /api/assets': [refused(503, { detail: 'The server is busy. Try again.' }), ok(CAR)],
      })
      renderShell()
      fireEvent.click(await screen.findByRole('button', { name: 'Add asset' }))
      click('Vehicle')
      type(/^Name/, 'Test Car')
      typeMoney(/Worth today/, '52000')
      click('Continue')
      click('Add a loan')
      type('Loan name', 'Test Car Loan')
      typeMoney('Amount owed', '38000')
      click('Add loan')
      click('Continue')
      click('Add asset')

      expect((await screen.findByRole('alert')).textContent).toBe('The server is busy. Try again.')
      // The loan's data-changed signal refetched the page, and that refetch failed.
      await waitFor(() => expect(calls('GET /api/assets')).toBe(2))
      expect(document.querySelector('.ui-modal__title')?.textContent).toBe('How much did you pay?')
      expect(screen.queryByText('Something went wrong')).toBeNull()

      click('Add asset')
      await waitFor(() => expect(calls('POST /api/assets')).toBe(2))
      expect(calls('POST /api/accounts')).toBe(1)
    },
    SHELL_TIMEOUT,
  )
})

describe('Assets — the create wiring', () => {
  it(
    'starts at step 2 with the category whose chip is active',
    async () => {
      serve({ 'GET /api/assets': [ok(payload([CAR]))] })
      renderShell()
      fireEvent.click(await screen.findByRole('button', { name: /^Vehicles/ }))
      click('Add asset')
      expect(document.querySelector('.ui-modal__title')?.textContent).toBe('Name and value')
      expect(subtitle()).toBe('Vehicle · Step 2 of 4')
    },
    SHELL_TIMEOUT,
  )

  it(
    "closes, confirms, and opens the new asset's page before the refetch lists it",
    async () => {
      let listed: (r: Response) => void = () => {}
      serve({
        'GET /api/assets': [
          ok(payload([])),
          new Promise<Response>((resolve) => (listed = resolve)),
        ],
        'POST /api/assets': [ok(WATCH)],
      })
      renderShell()
      fireEvent.click(await screen.findByRole('button', { name: 'Add asset' }))
      click('Watch & jewellery')
      type(/^Name/, 'Test Watch')
      typeMoney(/Worth today/, '14800')
      click('Continue')
      click('Continue')
      click('Add asset')

      await waitFor(() => expect(document.querySelector('.as-wizard')).toBeNull())
      expect(document.querySelector('.as-toast')?.textContent).toBe('Test Watch added')
      // Still the old list — the page is drawn from the asset the server just returned.
      expect(document.querySelector('.as-dtitle')?.textContent).toBe('Test Watch')

      listed(ok(payload([WATCH])))
      await waitFor(() => expect(calls('GET /api/assets')).toBe(2))
      expect(document.querySelector('.as-dtitle')?.textContent).toBe('Test Watch')
    },
    SHELL_TIMEOUT,
  )
})

describe('Assets — moving to another asset', () => {
  it(
    "starts the next asset's page afresh — a dialog opened on the last one never carries over",
    async () => {
      serve({ 'GET /api/assets': [ok(payload([CAR, WATCH]))] })
      const props = { onAskLucius: vi.fn(), onNavigate: vi.fn(), onSelect: vi.fn() }
      const { rerender } = render(<Assets {...props} selectedId={CAR.id} />)
      fireEvent.click(await screen.findByRole('button', { name: 'Update value' }))
      expect(subtitle()).toBe('Test Car')

      rerender(<Assets {...props} selectedId={WATCH.id} />)
      expect(document.querySelector('.as-dtitle')?.textContent).toBe('Test Watch')
      // Not the car's Update value dialog, now pointed at the watch.
      expect(document.querySelector('.ui-modal__title')).toBeNull()
    },
    SHELL_TIMEOUT,
  )
})

describe('Assets — a category is somewhere you can go back from (owner, 2026-09-28)', () => {
  it(
    "opens a stack into the URL as the category's own cards; the All chip goes back",
    async () => {
      serve({ 'GET /api/assets': [ok(payload(watches))] })
      const props = { onAskLucius: vi.fn(), onNavigate: vi.fn(), onSelect: vi.fn() }
      const onSelectCat = vi.fn()
      const { rerender } = render(
        <Assets {...props} selectedCat={null} onSelectCat={onSelectCat} />,
      )
      fireEvent.click(await screen.findByRole('button', { name: /4 items/ }))
      expect(onSelectCat).toHaveBeenLastCalledWith('watch')

      rerender(<Assets {...props} selectedCat="watch" onSelectCat={onSelectCat} />)
      expect(document.querySelector('.as-stack')).toBeNull()
      expect(document.querySelectorAll('.as-grid > .as-card')).toHaveLength(4)
      // The way back is the All chip (or back, the URL remembering the category) — no button.
      expect(screen.queryByRole('button', { name: /All assets/ })).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: /^All/ }))
      expect(onSelectCat).toHaveBeenLastCalledWith(null)
    },
    SHELL_TIMEOUT,
  )
})

describe('Assets — where the register was scrolled (owner, 2026-10-03)', () => {
  const props = { onAskLucius: vi.fn(), onNavigate: vi.fn(), onSelect: vi.fn() }

  // A phone scrolls .app-layout; opening a category or an asset changes only the search, never
  // the path the shell resets scroll on. jsdom keeps scrollTop but has no Element.scrollTo.
  function renderScrolled(extra: { selectedCat: string | null; selectedId?: string | null }) {
    Element.prototype.scrollTo = function (this: Element, opts?: ScrollToOptions | number) {
      this.scrollTop = typeof opts === 'object' ? (opts.top ?? 0) : 0
    }
    const tree = (e: typeof extra) => (
      <div className="app-layout">
        <Assets {...props} onSelectCat={vi.fn()} {...e} />
      </div>
    )
    const view = render(tree(extra))
    return {
      layout: view.container.querySelector('.app-layout') as HTMLElement,
      go: (e: typeof extra) => view.rerender(tree(e)),
    }
  }
  afterEach(() => {
    delete (Element.prototype as { scrollTo?: unknown }).scrollTo
    vi.restoreAllMocks()
  })

  it(
    'opens a category at its top, not at the offset the All register was scrolled to',
    async () => {
      serve({ 'GET /api/assets': [ok(payload(watches))] })
      const { layout, go } = renderScrolled({ selectedCat: null })
      fireEvent.click(await screen.findByRole('button', { name: /4 items/ }))
      layout.scrollTop = 814
      go({ selectedCat: 'watch' })
      expect(layout.scrollTop).toBe(0)
    },
    SHELL_TIMEOUT,
  )

  it(
    'returns from an asset page to the same place in the register, however Back is done',
    async () => {
      serve({ 'GET /api/assets': [ok(payload(watches))] })
      const { layout, go } = renderScrolled({ selectedCat: 'watch' })
      layout.scrollTop = 900
      fireEvent.click(await screen.findByText('Watch 3'))
      go({ selectedCat: 'watch', selectedId: 'oa_watch00003' })
      expect(layout.scrollTop).toBe(0)
      go({ selectedCat: 'watch', selectedId: null })
      expect(layout.scrollTop).toBe(900)
    },
    SHELL_TIMEOUT,
  )
})
