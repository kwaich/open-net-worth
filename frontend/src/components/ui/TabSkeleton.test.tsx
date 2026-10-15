// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TabSkeleton } from './TabSkeleton'

describe('TabSkeleton', () => {
  it.each([
    ['cards', 'Loading assets…'],
    ['workspace', 'Loading skills…'],
    ['list', 'Loading automations…'],
  ] as const)('renders the %s layout as a labelled busy region', (variant, label) => {
    const { container } = render(<TabSkeleton variant={variant} label={label} />)

    expect(screen.getByRole('status', { name: label })).toBeTruthy()
    expect(container.querySelectorAll('.ui-skeleton').length).toBeGreaterThan(3)
  })

  it('models Accounts as its navigator, overview, filters, and transaction register', () => {
    const { container } = render(<TabSkeleton variant="accounts" label="Loading accounts…" />)

    expect(container.querySelector('.ui-tab-skeleton__accounts-sidebar')).toBeTruthy()
    expect(container.querySelectorAll('.ui-tab-skeleton__account-summary')).toHaveLength(4)
    expect(container.querySelectorAll('.ui-tab-skeleton__account-register-row')).toHaveLength(8)
    expect(container.querySelector('.ui-tab-skeleton__accounts-mobile-cash')).toBeTruthy()
    expect(container.querySelector('.ui-tab-skeleton__accounts-mobile-controls')).toBeTruthy()
    expect(container.querySelectorAll('.ui-tab-skeleton__accounts-mobile-row')).toHaveLength(6)
  })

  it('models the Assets register as KPIs, filters, then tall asset cards', () => {
    const { container } = render(<TabSkeleton variant="assets" label="Loading assets…" />)

    expect(container.querySelectorAll('.ui-tab-skeleton__asset-kpi')).toHaveLength(4)
    expect(container.querySelectorAll('.ui-tab-skeleton__filter-chip')).toHaveLength(5)
    expect(container.querySelectorAll('.ui-tab-skeleton__asset-card')).toHaveLength(5)
    expect(
      container.querySelector(
        '.ui-tab-skeleton__assets-page-head .ui-tab-skeleton__page-head-title',
      ),
    ).toBeTruthy()
  })

  it('models the Budget view as a summary and envelope table, not generic cards', () => {
    const { container } = render(<TabSkeleton variant="budget" label="Loading budget…" />)

    expect(container.querySelector('.ui-tab-skeleton__budget-summary')).toBeTruthy()
    expect(container.querySelectorAll('.ui-tab-skeleton__budget-row')).toHaveLength(7)
  })

  it('models Commitments as its funding masthead followed by commitment rows', () => {
    const { container } = render(<TabSkeleton variant="commitments" label="Loading commitments…" />)

    expect(container.querySelector('.ui-tab-skeleton__commitment-masthead')).toBeTruthy()
    expect(container.querySelectorAll('.ui-tab-skeleton__commitment-row')).toHaveLength(4)
  })

  it('models Automations as controls and rule rows, not generic content cards', () => {
    const { container } = render(<TabSkeleton variant="automations" label="Loading automations…" />)

    expect(container.querySelector('.ui-tab-skeleton__automation-controls')).toBeTruthy()
    expect(container.querySelectorAll('.ui-tab-skeleton__automation-card')).toHaveLength(6)
  })

  it('models Skills as its tree and detail workspace', () => {
    const { container } = render(<TabSkeleton variant="skills" label="Loading skills…" />)

    expect(container.querySelector('.ui-tab-skeleton__skills-tree')).toBeTruthy()
    expect(container.querySelectorAll('.ui-tab-skeleton__skills-tree-row')).toHaveLength(7)
  })

  it('models Connectors as masthead filters and connector rows', () => {
    const { container } = render(<TabSkeleton variant="connectors" label="Loading connectors…" />)

    expect(container.querySelector('.ui-tab-skeleton__ph-toolbar')).toBeTruthy()
    expect(container.querySelector('.ui-tab-skeleton__ph-tabs')).toBeTruthy()
    expect(container.querySelectorAll('.ui-tab-skeleton__connector-row')).toHaveLength(5)
  })

  it.each([
    ['dashboard', 'ui-tab-skeleton__dashboard-widgets'],
    ['inbox', 'ui-tab-skeleton__inbox-sections'],
    ['holdings', 'ui-tab-skeleton__holdings-masthead'],
    ['performance', 'ui-tab-skeleton__performance-chart'],
    ['income', 'ui-tab-skeleton__income-columns'],
    ['activity', 'ui-tab-skeleton__activity-table'],
    ['options', 'ui-tab-skeleton__options-table'],
    ['equity', 'ui-tab-skeleton__equity-timeline'],
    ['cashflow', 'ui-tab-skeleton__cashflow-chart'],
    ['categories', 'ui-tab-skeleton__categories-table'],
    ['intel', 'ui-tab-skeleton__intel-stories'],
    ['builder', 'ui-tab-skeleton__builder-compose'],
    ['help', 'ui-tab-skeleton__help-layout'],
  ] as const)('uses a screen-shaped %s layout', (variant, selector) => {
    const { container } = render(<TabSkeleton variant={variant} label={`Loading ${variant}…`} />)

    expect(container.querySelector(`.${selector}`)).toBeTruthy()
  })

  it('models Investments as its portfolio masthead, brief, tabs, controls, and holding cards', () => {
    const { container } = render(<TabSkeleton variant="holdings" label="Loading investments…" />)

    expect(container.querySelector('.ui-tab-skeleton__holdings-masthead')).toBeTruthy()
    expect(container.querySelector('.ui-tab-skeleton__holdings-brief')).toBeTruthy()
    expect(container.querySelector('.ui-tab-skeleton__holdings-tabs')).toBeTruthy()
    expect(
      container.querySelectorAll('.ui-tab-skeleton__holdings-tabs > .ui-skeleton'),
    ).toHaveLength(7)
    expect(container.querySelectorAll('.ui-tab-skeleton__holding-card')).toHaveLength(6)
  })

  it.each([
    ['assets', 'ui-tab-skeleton__assets-page-head'],
    ['commitments', 'ui-tab-skeleton__commitments-page-head'],
    ['automations', 'ui-tab-skeleton__automations-page-head'],
    ['skills', 'ui-tab-skeleton__skills-page-head'],
    ['connectors', 'ui-tab-skeleton__connectors-page-head'],
    ['dashboard', 'ui-tab-skeleton__dashboard-page-head'],
    ['inbox', 'ui-tab-skeleton__inbox-page-head'],
    ['intel', 'ui-tab-skeleton__intel-page-head'],
    ['builder', 'ui-tab-skeleton__builder-page-head'],
  ] as const)('uses its own page header for the %s screen', (variant, selector) => {
    const { container } = render(<TabSkeleton variant={variant} label={`Loading ${variant}…`} />)

    expect(container.querySelector(`.${selector}`)).toBeTruthy()
    expect(container.querySelector('.ui-tab-skeleton__header')).toBeNull()
  })

  it('models 50/30/20 as its month bar, hero, and envelope table (cards on a phone)', () => {
    const { container } = render(<TabSkeleton variant="proportional" label="Loading…" />)

    expect(container.querySelector('.ui-tab-skeleton__budget-page-head')).toBeTruthy()
    expect(container.querySelector('.ui-tab-skeleton__prop-bar')).toBeTruthy()
    expect(container.querySelectorAll('.ui-tab-skeleton__prop-row--head')).toHaveLength(1)
    expect(container.querySelectorAll('.ui-tab-skeleton__prop-card')).toHaveLength(5)
  })

  it('keeps Investments focused on its portfolio content because the shell owns its title', () => {
    const { container } = render(<TabSkeleton variant="holdings" label="Loading investments…" />)

    expect(container.querySelector('.ui-tab-skeleton__holdings-page-head')).toBeNull()
    expect(container.querySelector('.ui-tab-skeleton__header')).toBeNull()
  })
})
