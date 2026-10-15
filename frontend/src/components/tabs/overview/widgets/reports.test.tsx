// @vitest-environment jsdom
//
// The Reports widgets read ONE server aggregate (`GET /api/overview/reports`) — the per-month
// category rows, their totals, the stacked series, the merchants, the savings rate and the
// cash-flow columns all arrive computed (ADR 2026-07-31-overview-aggregates-are-server-
// computed). They used to make 12 parallel planning fetches and fold the results in the
// browser.
//
// The original regression this file guards still applies, one layer down: the income side of
// the payload is shaped differently from the expense side, and a fixture echoing the
// frontend's own interface instead of the real server response kept that bug invisible. So
// the fixture below is the real `/api/overview/reports` shape.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

// jsdom has no canvas, so a chart never builds: capture the config each OvChart is handed and
// assert on that — the datasets, the options and the click handler are the contract.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type ChartCfg = Record<string, any>
const charts = vi.hoisted(() => ({ configs: [] as ChartCfg[] }))
vi.mock('../charts', async (orig) => ({
  ...(await orig<typeof import('../charts')>()),
  OvChart: ({ config }: { config: ChartCfg }) => {
    charts.configs.push(config)
    return <canvas data-testid="ov-chart" />
  },
}))

import { reportsWidgetComponents } from './reports'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'

afterEach(() => {
  cleanup()
  authFetch.mockReset()
  charts.configs.length = 0
})

const EXPENSE_ROWS = [
  { key: 'env_rent', label: 'Rent', color: '', amount: 2150 },
  { key: 'env_groceries', label: 'Groceries', color: '', amount: 52.11 },
]
const INCOME_ROWS = [{ key: 'cat_salary', label: 'Salary', color: '#3b86dd', amount: 8000 }]

const POINT = {
  ym: '2026-07',
  expense_rows: EXPENSE_ROWS,
  expense_total: 2202.11,
  income_rows: INCOME_ROWS,
  income_total: 8000,
}

const REPORTS_PAYLOAD = {
  base_currency: 'SGD',
  months: ['2026-07'],
  points: [POINT],
  current: POINT,
  expense_series: EXPENSE_ROWS.map((r) => ({ ...r, data: [r.amount] })),
  income_series: INCOME_ROWS.map((r) => ({ ...r, data: [r.amount] })),
  expense_table: EXPENSE_ROWS.map((r) => ({
    ...r,
    current: r.amount,
    average: r.amount,
    total: r.amount,
  })),
  income_table: INCOME_ROWS.map((r) => ({
    ...r,
    current: r.amount,
    average: r.amount,
    total: r.amount,
  })),
  expense_totals: { current: 2202.11, average: 2202.11, total: 2202.11 },
  income_totals: { current: 8000, average: 8000, total: 8000 },
  net_totals: { current: 5797.89, average: 5797.89, total: 5797.89 },
  avg_monthly_spend: 2202.11,
  net_total: 5797.89,
  spending_month: '2026-07',
  current_month: '2026-07',
  merchants: [
    { key: 'pay_1', label: 'Landlord', amount: 2150, pct: 97.63 },
    { key: 'd:corner shop', label: 'CORNER SHOP 123', amount: 52.11, pct: 2.37 },
  ],
  savings: { income: 8000, spent: 2202.11, saved: 5797.89, rate_pct: 72.47, known: true },
}

// GET /api/cashflow/flow?compact=true — the real server shape (services/cash_flow_graph.py).
const FLOW_PAYLOAD = {
  month: '2026-07',
  base_currency: 'SGD',
  income_total: 8000,
  spend_total: 2202.11,
  saved: 5797.89,
  shortfall: 0,
  flow_total: 8000,
  savings_rate_pct: 72.5,
  columns: 3,
  nodes: [
    { id: 'total', label: 'Income', column: 1, kind: 'total', amount: 8000, pct: 100 },
    { id: 'inc:cat_salary', label: 'Salary', column: 0, kind: 'income', amount: 8000, pct: 100 },
    {
      id: 'sg:g_home',
      label: 'Housing',
      column: 2,
      kind: 'spend_group',
      amount: 2202.11,
      pct: 27.5,
      color_slot: 0,
    },
    { id: 'saved', label: 'Saved', column: 2, kind: 'saved', amount: 5797.89, pct: 72.5 },
  ],
  links: [
    { source: 'inc:cat_salary', target: 'total', amount: 8000 },
    { source: 'total', target: 'sg:g_home', amount: 2202.11 },
    { source: 'total', target: 'saved', amount: 5797.89 },
  ],
}

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

// October has begun and nothing is spent in it yet: the spending widgets show September.
const NEW_MONTH = { ...POINT, ym: '2026-10', expense_rows: [], expense_total: 0 }
const NEW_MONTH_PAYLOAD = {
  ...REPORTS_PAYLOAD,
  months: ['2026-09', '2026-10'],
  points: [{ ...POINT, ym: '2026-09' }, NEW_MONTH],
  current: NEW_MONTH,
  spending_month: '2026-09',
  current_month: '2026-10',
}

function routeToFixtures(payload: unknown = REPORTS_PAYLOAD) {
  authFetch.mockImplementation((url: string) =>
    url.startsWith('/api/reports/rollup')
      ? jsonOk(payload)
      : url.startsWith('/api/cashflow/flow')
        ? jsonOk(FLOW_PAYLOAD)
        : jsonOk({}),
  )
}

function instanceOf(defId: string, config: WidgetInstance['config'] = {}): WidgetInstance {
  return { instanceId: `w-${defId}`, defId, x: 0, y: 0, w: 6, h: 300, config }
}

function renderWidget(defId: string, config: WidgetInstance['config'] = {}) {
  const Comp = reportsWidgetComponents[defId]!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === defId)!
  return render(
    <Comp instance={instanceOf(defId, config)} def={def} onPatch={vi.fn()} onNavigate={vi.fn()} />,
  )
}

const lastChart = () => charts.configs[charts.configs.length - 1]!

describe('Reports widgets render the server aggregate', () => {
  it('renders spending rows instead of the load-failed placeholder', async () => {
    routeToFixtures()
    renderWidget('rep-spendcat')

    await waitFor(() => expect(screen.getByText('Rent')).toBeTruthy())
    expect(screen.getByText('Groceries')).toBeTruthy()
    expect(screen.queryByText(/Couldn't load/)).toBeNull()
  })

  it('says nothing is spent yet this month only when no earlier month has spending either', async () => {
    routeToFixtures({
      ...NEW_MONTH_PAYLOAD,
      points: [NEW_MONTH],
      spending_month: '2026-10',
    })
    renderWidget('rep-spendcat')

    await waitFor(() =>
      expect(screen.getByText('No spending recorded this month yet.')).toBeTruthy(),
    )
  })

  it('shows the latest month with spending, labelled, while the new month has none', async () => {
    routeToFixtures(NEW_MONTH_PAYLOAD)
    renderWidget('rep-spendcat')

    await waitFor(() => expect(screen.getByText('Rent')).toBeTruthy())
    expect(screen.getByText('September. Nothing spent in October yet.')).toBeTruthy()
    expect(screen.queryByText(/No spending recorded/)).toBeNull()
  })

  it('draws the cash-flow Sankey from the compact flow graph', async () => {
    routeToFixtures()
    const { container } = renderWidget('rep-sankey')

    await waitFor(() => expect(screen.getByText('Salary')).toBeTruthy())
    expect(screen.getByText('Housing')).toBeTruthy()
    expect(screen.getByText('Saved')).toBeTruthy()
    expect(container.querySelectorAll('.mf-lnk')).toHaveLength(3)
    expect(authFetch.mock.calls.some(([u]) => String(u).includes('compact=true'))).toBe(true)
    expect(screen.queryByText(/Couldn't load/)).toBeNull()
  })

  it('still surfaces the error placeholder when the request genuinely fails', async () => {
    authFetch.mockRejectedValue(new Error('network down'))
    renderWidget('rep-spendcat')

    await waitFor(() => expect(screen.getByText(/Couldn't load the monthly report/)).toBeTruthy())
  })

  // Top merchants was a canvas bar chart: amounts hid behind a tooltip a phone tap never showed
  // (the tap navigated away first), and only the bar area answered taps, never the name.
  describe('Top merchants is a ranked list', () => {
    it('prints every amount and share, and a row opens that merchant this month', async () => {
      routeToFixtures()
      const onOpenRegister = vi.fn()
      const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'rep-merchants')!
      const Comp = reportsWidgetComponents['rep-merchants']!
      render(
        <Comp
          instance={instanceOf('rep-merchants')}
          def={def}
          onPatch={vi.fn()}
          onOpenRegister={onOpenRegister}
        />,
      )

      const row = await screen.findByRole('button', { name: /Landlord/ })
      expect(row.textContent).toMatch(/2,150/)
      expect(row.textContent).toMatch(/97\.6/)
      fireEvent.click(row)
      expect(onOpenRegister).toHaveBeenCalledWith({ payee: 'Landlord', month: '2026-07' })

      // No payee to filter by: shown, but not a button that would open an empty register.
      expect(screen.getByText('CORNER SHOP 123')).toBeTruthy()
      expect(screen.queryByRole('button', { name: /CORNER SHOP/ })).toBeNull()
    })
  })
})

describe('Top merchants in a new month', () => {
  it('ranks the month before, labelled, and a row opens that month', async () => {
    routeToFixtures(NEW_MONTH_PAYLOAD)
    const onOpenRegister = vi.fn()
    const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'rep-merchants')!
    const Comp = reportsWidgetComponents['rep-merchants']!
    render(
      <Comp
        instance={instanceOf('rep-merchants')}
        def={def}
        onPatch={vi.fn()}
        onOpenRegister={onOpenRegister}
      />,
    )

    const row = await screen.findByRole('button', { name: /Landlord/ })
    expect(screen.getByText('September. Nothing spent in October yet.')).toBeTruthy()
    fireEvent.click(row)
    expect(onOpenRegister).toHaveBeenCalledWith({ payee: 'Landlord', month: '2026-09' })
  })
})

describe('Top merchants "Show as"', () => {
  function renderMerchants(viz: string | undefined, onOpenRegister = vi.fn()) {
    const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'rep-merchants')!
    const Comp = reportsWidgetComponents['rep-merchants']!
    render(
      <Comp
        instance={instanceOf('rep-merchants', viz ? { viz } : {})}
        def={def}
        onPatch={vi.fn()}
        onOpenRegister={onOpenRegister}
      />,
    )
    return onOpenRegister
  }

  it('offers List, Bar and Pie, defaulting to the list', async () => {
    routeToFixtures()
    renderMerchants(undefined)

    await screen.findByRole('button', { name: /Landlord/ })
    expect(charts.configs).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Chart type' }))
    for (const label of ['List', 'Bar', 'Pie']) {
      expect(screen.getByRole('menuitemradio', { name: label })).toBeTruthy()
    }
  })

  it('Bar draws the merchants and a click on a bar opens that merchant this month', async () => {
    routeToFixtures()
    const onOpenRegister = renderMerchants('bar')

    await waitFor(() => expect(charts.configs.length).toBeGreaterThan(0))
    const cfg = lastChart()
    expect(cfg.type).toBe('bar')
    expect(cfg.data.labels).toEqual(['Landlord', 'CORNER SHOP 123'])
    expect(cfg.data.datasets[0].data).toEqual([2150, 52.11])

    cfg.options.onClick({}, [{ index: 0 }])
    expect(onOpenRegister).toHaveBeenCalledWith({ payee: 'Landlord', month: '2026-07' })
    // A description-grouped row has no payee to filter by.
    cfg.options.onClick({}, [{ index: 1 }])
    expect(onOpenRegister).toHaveBeenCalledTimes(1)
  })

  it('Pie draws a donut with a legend whose rows open the merchant', async () => {
    routeToFixtures()
    const onOpenRegister = renderMerchants('pie')

    const row = await screen.findByRole('button', { name: /Landlord/ })
    expect(lastChart().type).toBe('doughnut')
    expect(row.textContent).toMatch(/97\.6/)
    fireEvent.click(row)
    expect(onOpenRegister).toHaveBeenCalledWith({ payee: 'Landlord', month: '2026-07' })
    expect(screen.queryByRole('button', { name: /CORNER SHOP/ })).toBeNull()
  })
})

describe('Income vs expense separates the two sides', () => {
  it('defaults to Totals: an Income bar in green and an Expenses bar in red per month', async () => {
    routeToFixtures()
    renderWidget('rep-incexp')

    await waitFor(() => expect(charts.configs.length).toBeGreaterThan(0))
    const ds = lastChart().data.datasets
    expect(ds.map((d: { label: string }) => d.label)).toEqual(['Income', 'Expenses'])
    expect(ds[0].data).toEqual([8000])
    expect(ds[1].data).toEqual([2202.11])
    expect(ds[0].stack).not.toBe(ds[1].stack)
    expect(lastChart().options.plugins.legend.display).toBe(false)
  })

  it('By category legend is two labelled groups, with a tooltip naming the side', async () => {
    routeToFixtures()
    const { container } = renderWidget('rep-incexp', { detail: 'By category' })

    await waitFor(() => expect(container.querySelectorAll('.ov-sleg-group')).toHaveLength(2))
    const [income, expenses] = Array.from(container.querySelectorAll('.ov-sleg-group'))
    expect(income!.querySelector('.ov-sleg-title')!.textContent).toBe('Income')
    expect(income!.textContent).toContain('Salary')
    expect(income!.textContent).not.toContain('Rent')
    expect(expenses!.querySelector('.ov-sleg-title')!.textContent).toBe('Expenses')
    expect(expenses!.textContent).toContain('Rent')
    expect(expenses!.textContent).toContain('Groceries')

    const cfg = lastChart()
    const label = cfg.options.plugins.tooltip.callbacks.label
    const salary = cfg.data.datasets.find((d: { label: string }) => d.label === 'Salary')
    const rent = cfg.data.datasets.find((d: { label: string }) => d.label === 'Rent')
    expect(label({ dataset: salary, raw: 8000 })).toMatch(/^Income · Salary: .*8,000/)
    expect(label({ dataset: rent, raw: 2150 })).toMatch(/^Expense · Rent: .*2,150/)
  })

  it('folds a long side behind "+N more" and names each remainder by its side', async () => {
    const series = (n: number, side: 'in' | 'out') => [
      ...Array.from({ length: n }, (_, i) => ({
        key: `${side}${i}`,
        label: `${side === 'in' ? 'Inc' : 'Exp'} ${i}`,
        color: '',
        data: [i + 1],
      })),
      { key: '__other__', label: 'All other', color: '', data: [5] },
    ]
    authFetch.mockImplementation((url: string) =>
      url.startsWith('/api/reports/rollup')
        ? jsonOk({
            ...REPORTS_PAYLOAD,
            income_series: series(4, 'in'),
            expense_series: series(6, 'out'),
          })
        : jsonOk({}),
    )
    const { container } = renderWidget('rep-incexp', { detail: 'By category' })

    await waitFor(() => expect(container.querySelectorAll('.ov-sleg-more.c3')).toHaveLength(2))
    expect(
      Array.from(container.querySelectorAll('.ov-sleg-more.c3')).map((b) => b.textContent),
    ).toEqual(['+2 more', '+4 more'])
    const labels = lastChart().data.datasets.map((d: { label: string }) => d.label)
    expect(labels).toContain('All other income')
    expect(labels).toContain('All other expenses')
  })
})

describe('Spending trends shows the average', () => {
  it('adds a dashed line at the server average over complete months, and says so in the tooltip', async () => {
    authFetch.mockImplementation((url: string) =>
      url.startsWith('/api/reports/rollup')
        ? jsonOk({
            ...REPORTS_PAYLOAD,
            points: [POINT, { ...POINT, ym: '2026-08', expense_total: 900 }],
            expense_series: EXPENSE_ROWS.map((r) => ({ ...r, data: [r.amount, 0] })),
            expense_totals: { current: 900, average: 2202.11, total: 3102.11 },
          })
        : jsonOk({}),
    )
    renderWidget('rep-trends')

    await waitFor(() => expect(charts.configs.length).toBeGreaterThan(0))
    const cfg = lastChart()
    const avg = cfg.data.datasets.find((d: { type?: string }) => d.type === 'line')
    expect(avg.data).toEqual([2202.11, 2202.11])
    expect(avg.borderDash).toBeTruthy()
    expect(cfg.options.plugins.legend.display).toBe(false)
    expect(cfg.plugins.map((p: { id: string }) => p.id)).toContain('ovAvgLabel')
    expect(cfg.options.plugins.tooltip.callbacks.label({ dataset: avg, raw: 2202.11 })).toMatch(
      /average.*2,202/i,
    )
  })
})
