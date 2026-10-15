// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { WidgetShell } from './WidgetShell'
import { GridItemContext } from './gridItemContext'
import type { GridItemContextValue } from './gridItemContext'

afterEach(() => {
  cleanup()
})

function baseCtx(overrides: Partial<GridItemContextValue> = {}): GridItemContextValue {
  return {
    instanceId: 'w1',
    editing: false,
    onRenameTitle: vi.fn(),
    collapsed: false,
    onToggleCollapse: vi.fn(),
    onOpenFullscreen: vi.fn(),
    menuItems: [{ label: 'Display…', onClick: vi.fn() }],
    ...overrides,
  }
}

describe('WidgetShell mobile header kebab', () => {
  it('offers full screen, collapse, and the shared menu items via one header kebab', () => {
    const ctx = baseCtx()

    render(
      <GridItemContext.Provider value={ctx}>
        <WidgetShell base="Net worth">Widget content</WidgetShell>
      </GridItemContext.Provider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Widget options' }))
    // getByRole throws (failing the test) if the item is missing — finding
    // it is itself the assertion.
    screen.getByRole('menuitem', { name: 'Full screen' })
    screen.getByRole('menuitem', { name: 'Collapse' })
    screen.getByRole('menuitem', { name: 'Display…' })

    fireEvent.click(screen.getByRole('menuitem', { name: 'Collapse' }))
    expect(ctx.onToggleCollapse).toHaveBeenCalledOnce()
  })

  it('labels the toggle item Expand when the widget is already collapsed', () => {
    render(
      <GridItemContext.Provider value={baseCtx({ collapsed: true })}>
        <WidgetShell base="Net worth">Widget content</WidgetShell>
      </GridItemContext.Provider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Widget options' }))
    screen.getByRole('menuitem', { name: 'Expand' })
  })

  it('hides the header kebab while Customize mode is active', () => {
    render(
      <GridItemContext.Provider value={baseCtx({ editing: true })}>
        <WidgetShell base="Net worth">Widget content</WidgetShell>
      </GridItemContext.Provider>,
    )

    expect(screen.queryByRole('button', { name: 'Widget options' })).toBeNull()
  })

  it('renders nothing extra when there is no grid-item context (unknown-widget fallback)', () => {
    render(<WidgetShell base="Unknown widget">Widget content</WidgetShell>)

    expect(screen.queryByRole('button', { name: 'Widget options' })).toBeNull()
  })

  // Regression: WidgetFullscreen renders a widget's own <Content> (and so
  // this same WidgetShell) inside ITS OWN GridItemContext.Provider, one that
  // only ever supplies instanceId/editing/icon/onRenameTitle — collapsed/
  // onToggleCollapse/onOpenFullscreen/menuItems don't apply inside an
  // already-open fullscreen view. Before these fields were made optional,
  // this shape crashed WidgetShell on mount (`...ctx.menuItems` spreading
  // `undefined`) the instant ANY widget was opened fullscreen.
  it('does not crash and renders no header kebab under a fullscreen-shaped context (no quick-action fields)', () => {
    const fullscreenLikeCtx = {
      instanceId: 'w1',
      editing: false,
      onRenameTitle: vi.fn(),
    }

    expect(() =>
      render(
        <GridItemContext.Provider value={fullscreenLikeCtx}>
          <WidgetShell base="Net worth">Widget content</WidgetShell>
        </GridItemContext.Provider>,
      ),
    ).not.toThrow()

    expect(screen.queryByRole('button', { name: 'Widget options' })).toBeNull()
  })

  // The ⋯ sits inside the card, whose overflow clipping hid an inline popover on desktop: the
  // menu opened, invisibly. It must render outside the card.
  it('renders the header menu outside the card so the card cannot clip it', () => {
    const { container } = render(
      <GridItemContext.Provider value={baseCtx()}>
        <WidgetShell base="Net worth">Widget content</WidgetShell>
      </GridItemContext.Provider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Widget options' }))

    const menu = screen.getByRole('menu')
    expect(container.contains(menu)).toBe(false)
    expect(document.body.contains(menu)).toBe(true)
  })
})
