// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GridItemContext } from './gridItemContext'
import { WidgetShell } from './WidgetShell'

afterEach(cleanup)

describe('WidgetShell mobile actions', () => {
  it('uses a vertical ellipsis for the widget options trigger', () => {
    render(
      <GridItemContext.Provider
        value={
          {
            instanceId: 'cash-flow',
            editing: false,
            menuItems: [{ label: 'Display…', onClick: vi.fn() }],
          } as never
        }
      >
        <WidgetShell base="Cash Flow">Widget content</WidgetShell>
      </GridItemContext.Provider>,
    )

    const trigger = screen.getByRole('button', { name: 'Widget options' })
    expect(trigger.querySelector('svg')).not.toBeNull()
    expect(trigger.textContent).not.toContain('⋯')
  })

  it('puts full screen, collapse, and widget actions in the header menu', () => {
    const openFullscreen = vi.fn()
    const toggleCollapse = vi.fn()
    const display = vi.fn()

    render(
      <GridItemContext.Provider
        value={
          {
            instanceId: 'cash-flow',
            editing: false,
            onRenameTitle: vi.fn(),
            collapsed: false,
            onOpenFullscreen: openFullscreen,
            onToggleCollapse: toggleCollapse,
            menuItems: [{ label: 'Display…', onClick: display }],
          } as never
        }
      >
        <WidgetShell base="Cash Flow">Widget content</WidgetShell>
      </GridItemContext.Provider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Widget options' }))

    expect(screen.getByRole('menuitem', { name: 'Full screen' })).not.toBeNull()
    expect(screen.getByRole('menuitem', { name: 'Collapse' })).not.toBeNull()
    expect(screen.getByRole('menuitem', { name: 'Display…' })).not.toBeNull()

    fireEvent.click(screen.getByRole('menuitem', { name: 'Collapse' }))
    expect(toggleCollapse).toHaveBeenCalledOnce()
  })
})
