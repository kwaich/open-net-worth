// @vitest-environment jsdom
//
// A widget's "⋯ → Edit…" opens only that widget's settings. Persona QA round 7: the page was
// left in layout-edit (Customize) mode afterwards and took more than one Cancel to leave. The
// dashboard's page state (`editing`) is owned by Customize alone: closing the editor, however it
// is closed, must leave the page where it was, and one Cancel leaves Customize.

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { ComputedRow } from '../../../types/portfolio'
import { Overview } from './Overview'

vi.mock('./registry', async () => {
  const { WidgetShell } = await import('./WidgetShell')
  return {
    WIDGET_COMPONENTS: {
      hero: () => <WidgetShell base="Net worth">hero</WidgetShell>,
    },
  }
})

vi.mock('./useDashboardLayout', () => {
  const widgets = [
    { instanceId: 'net-worth', defId: 'hero', x: 0, y: 0, w: 12, h: 224, config: {}, limit: null },
  ]
  const activeView = { viewId: 'overview', name: 'Overview', widgets }
  const layout = {
    loading: false,
    error: null,
    dismissError: vi.fn(),
    state: { activeViewId: 'overview', views: [activeView] },
    views: [activeView],
    activeView,
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
    addWidget: vi.fn(),
    addWidgetWithConfig: vi.fn(),
    addSection: vi.fn(),
    removeItem: vi.fn(),
    duplicateItem: vi.fn(),
    patchItem: vi.fn(),
    dragItem: vi.fn(),
    resizeItem: vi.fn(),
    newlyAddedId: null,
    clearNewlyAdded: vi.fn(),
  }
  return { useDashboardLayout: () => layout }
})

const rows = [{ label: 'Cash', value: 1 }] as unknown as ComputedRow[]
const inCustomize = () => !!document.querySelector('.ov-page--editing')

function openEditFromMenu() {
  fireEvent.click(screen.getAllByRole('button', { name: 'Widget options' })[0]!)
  fireEvent.click(screen.getByRole('menuitem', { name: 'Edit…' }))
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeTruthy()
}

describe('Dashboard widget "⋯ → Edit…"', () => {
  beforeAll(() => {
    global.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
  })
  afterEach(cleanup)

  it('opens the settings without Customize, and Save leaves the page as it was', () => {
    render(<Overview rows={rows} />)
    openEditFromMenu()
    expect(inCustomize()).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull()
    expect(inCustomize()).toBe(false)
    expect(screen.getByRole('button', { name: 'Customize overview' })).toBeTruthy()
  })

  it('Discard leaves the page out of Customize too', () => {
    render(<Overview rows={rows} />)
    openEditFromMenu()
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull()
    expect(inCustomize()).toBe(false)
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull()
  })

  it('inside Customize, closing a widget editor returns to Customize and one Cancel leaves it', () => {
    render(<Overview rows={rows} />)
    fireEvent.click(screen.getByRole('button', { name: 'Customize overview' }))
    expect(inCustomize()).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Edit widget' }))
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(inCustomize()).toBe(true)

    fireEvent.click(screen.getAllByRole('button', { name: 'Cancel' })[0]!)
    expect(inCustomize()).toBe(false)
    expect(screen.getByRole('button', { name: 'Customize overview' })).toBeTruthy()
  })
})
