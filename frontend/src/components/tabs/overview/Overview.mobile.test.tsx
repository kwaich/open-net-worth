// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { ComputedRow } from '../../../types/portfolio'
import { Overview } from './Overview'

const overviewCss = readFileSync('src/components/tabs/overview/overview.css', 'utf8')
const accountsCss = readFileSync('src/components/tabs/accounts/accounts.css', 'utf8')

vi.mock('./useDashboardLayout', () => ({
  useDashboardLayout: () => ({
    loading: false,
    error: null,
    state: { activeViewId: 'overview', views: [] },
    views: [],
    activeView: { viewId: 'overview', name: 'Overview', widgets: [] },
    activeViewId: 'overview',
    isDirty: false,
    pickerDefinitions: [],
    snapshot: vi.fn(),
    restoreSnapshot: vi.fn(),
    setActiveViewId: vi.fn(),
    addView: vi.fn(),
    renameView: vi.fn(),
    deleteView: vi.fn(),
    reorderViews: vi.fn(),
    addWidgetWithConfig: vi.fn(),
    addSection: vi.fn(),
  }),
}))

describe('Overview mobile header', () => {
  beforeAll(() => {
    global.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
  })

  afterEach(cleanup)

  it('renders Customize as an icon action with an accessible label', () => {
    const rows = [{ label: 'Cash', value: 1 }] as unknown as ComputedRow[]

    render(<Overview rows={rows} />)

    const customize = screen.getByRole('button', { name: 'Customize overview' })
    expect(customize.querySelector('svg')).not.toBeNull()
    expect(customize.querySelector('.ov-customize-label')).not.toBeNull()
  })

  it('pins the header block as one opaque surface across the viewport', () => {
    const headRule = overviewCss.match(/\.ov-pagehead\s*\{([^}]*)\}/)?.[1]

    expect(headRule).toContain('position: sticky')
    expect(headRule).toContain('top: var(--ov-topbar-h, 56px)')
    expect(headRule).toContain('background: var(--bg)')
    expect(headRule).toContain('box-shadow: 0 0 0 100vmax var(--bg)')
    expect(headRule).toContain('clip-path: inset(0 -100vmax)')

    for (const inner of ['.ov-pagehead > .ui-page-header', '.ov-view-tabs']) {
      const rule = overviewCss.match(
        new RegExp(inner.replace(/[.>]/g, (c) => '\\' + c) + '\\s*\\{([^}]*)\\}'),
      )?.[1]
      expect(rule).not.toContain('position: sticky')
    }
  })

  it('renders the header and the view tabs inside that one box', () => {
    const rows = [{ label: 'Cash', value: 1 }] as unknown as ComputedRow[]
    const { container } = render(<Overview rows={rows} />)
    const head = container.querySelector('.ov-pagehead')

    expect(head?.querySelector(':scope > .ui-page-header')).not.toBeNull()
    expect(head?.querySelector(':scope > .ov-view-tabs')).not.toBeNull()
  })

  it('clears the full rendered shell bar on tablet-sized document scrolling', () => {
    const topbar = document.createElement('div')
    topbar.className = 'topbar'
    Object.defineProperty(topbar, 'offsetHeight', { value: 76 })
    document.body.append(topbar)
    const originalWidth = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 })

    const rows = [{ label: 'Cash', value: 1 }] as unknown as ComputedRow[]
    const view = render(<Overview rows={rows} />)

    expect(document.documentElement.style.getPropertyValue('--ov-topbar-h')).toBe('76px')

    view.unmount()
    topbar.remove()
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth })
  })

  it('keeps long view names on one line in a horizontally scrolling tab strip', () => {
    const mobileRules = overviewCss.slice(overviewCss.indexOf('@media (max-width: 640px)'))
    const tabsRule = mobileRules.match(/\.ov-view-tabs\s*\{([^}]*)\}/)?.[1]
    const tabRule = mobileRules.match(/\.ov-view-tab\s*\{([^}]*)\}/)?.[1]

    expect(tabsRule).toContain('overflow-x: auto')
    expect(tabsRule).toContain('flex-wrap: nowrap')
    expect(tabRule).toContain('flex: none')
    expect(tabRule).toContain('white-space: nowrap')
  })

  it('moves session actions into a mobile dock while keeping view tools at the top', () => {
    const rows = [{ label: 'Cash', value: 1 }] as unknown as ComputedRow[]
    const { container } = render(<Overview rows={rows} />)

    fireEvent.click(screen.getByRole('button', { name: 'Customize overview' }))

    expect(container.querySelector('.ov-page--editing')).not.toBeNull()
    const tools = container.querySelector<HTMLElement>('.ov-page-actions__tools')
    expect(tools).not.toBeNull()
    expect(container.querySelector('.ov-page-actions__commit')).not.toBeNull()
    expect(within(tools!).getByRole('button', { name: 'Add section' })).not.toBeNull()
    expect(within(tools!).getByRole('button', { name: 'Add widget' })).not.toBeNull()

    const dock = container.querySelector('.ov-mobile-edit-dock')
    expect(dock).not.toBeNull()
    expect(dock?.classList.contains('mb-anchored')).toBe(true)
    expect(dock?.querySelector('button:nth-of-type(1)')?.textContent).toContain('Reset all')
    expect(dock?.querySelector('button:nth-of-type(2)')?.textContent).toContain('Cancel')
    expect(dock?.querySelector('button:nth-of-type(3)')?.textContent).toBe('Save')

    fireEvent.click(within(dock as HTMLElement).getByRole('button', { name: 'Save' }))
    expect(container.querySelector('.ov-page--editing')).toBeNull()
  })

  it('pins the add controls and view tabs together while Customize mode is active', () => {
    const mobileRules = overviewCss.slice(overviewCss.indexOf('@media (max-width: 640px)'))
    const editingHeadRule = mobileRules.match(/\.ov-page--editing \.ov-pagehead\s*\{([^}]*)\}/)?.[1]
    const editTabsRule = mobileRules.match(/\.ov-page--editing \.ov-view-tabs\s*\{([^}]*)\}/)?.[1]

    expect(editingHeadRule).toContain('display: flex')
    expect(editingHeadRule).toContain('position: sticky')
    expect(editingHeadRule).toContain('top: var(--ov-topbar-h, 0px)')
    expect(editingHeadRule).toContain('background: var(--bg)')
    expect(editTabsRule).toContain('position: relative')
  })

  it('matches the bulk transaction panel density and surface above navigation', () => {
    const baseDockRule = overviewCss.match(/\.ov-mobile-edit-dock\s*\{([^}]*)\}/)?.[1]
    const mobileRules = overviewCss.slice(overviewCss.indexOf('@media (max-width: 640px)'))
    const mobileDockRule = mobileRules.match(/\.ov-mobile-edit-dock\s*\{([^}]*)\}/)?.[1]
    const mobileDockButtonRule = mobileRules.match(
      /\.ov-mobile-edit-dock > \.ui-btn\s*\{([^}]*)\}/,
    )?.[1]
    const editingPageRule = mobileRules.match(/\.ov-page--editing\s*\{([^}]*)\}/)?.[1]
    const bulkPanelRule = accountsCss.match(/\.acct-bulk-inner\s*\{([^}]*)\}/)?.[1]

    expect(baseDockRule).toContain('display: none')
    expect(mobileDockRule).toContain('display: flex')
    expect(mobileDockRule).toContain('position: fixed')
    for (const declaration of [
      'background: var(--modal-bg)',
      'border: 1px solid var(--modal-border)',
      'border-radius: var(--radius-pill)',
      'box-shadow: var(--shadow-modal)',
      'padding: 8px 10px',
    ]) {
      expect(bulkPanelRule).toContain(declaration)
      expect(mobileDockRule).toContain(declaration)
    }
    expect(mobileDockButtonRule).toContain('height: 32px')
    expect(editingPageRule).toContain('padding-bottom:')
  })
})
