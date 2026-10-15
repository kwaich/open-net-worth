// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { PageTabs } from './PageTabs'
import type { PageTab } from './PageTabs'

// Tabs used across tests.
type Section = 'a' | 'b' | 'c'
const TABS: PageTab<Section>[] = [
  { key: 'a', label: 'Alpha' },
  { key: 'b', label: 'Beta', badge: 2 },
  { key: 'c', label: 'Gamma' },
]

describe('PageTabs — static markup (no DOM)', () => {
  it('renders a tablist with role=tab buttons', () => {
    const html = renderToStaticMarkup(
      createElement(PageTabs<Section>, {
        tabs: TABS,
        value: 'a',
        onChange: () => {},
      }),
    )
    expect(html).toContain('role="tablist"')
    expect(html).toContain('role="tab"')
  })

  it('active tab has aria-selected="true", others false', () => {
    const html = renderToStaticMarkup(
      createElement(PageTabs<Section>, {
        tabs: TABS,
        value: 'b',
        onChange: () => {},
      }),
    )
    // Simple string check — Beta button should have aria-selected="true"
    expect(html).toContain('aria-selected="true"')
    // There must be exactly one true and two false.
    const trueCount = (html.match(/aria-selected="true"/g) ?? []).length
    const falseCount = (html.match(/aria-selected="false"/g) ?? []).length
    expect(trueCount).toBe(1)
    expect(falseCount).toBe(2)
  })

  it('active tab has tabIndex 0, inactive tabs have tabIndex -1', () => {
    const html = renderToStaticMarkup(
      createElement(PageTabs<Section>, {
        tabs: TABS,
        value: 'a',
        onChange: () => {},
      }),
    )
    // renderToStaticMarkup emits lowercase attribute names (tabindex, not tabIndex).
    const zeroCount = (html.match(/tabindex="0"/g) ?? []).length
    const negCount = (html.match(/tabindex="-1"/g) ?? []).length
    expect(zeroCount).toBe(1)
    expect(negCount).toBe(2)
  })

  it('badge renders when provided', () => {
    const html = renderToStaticMarkup(
      createElement(PageTabs<Section>, {
        tabs: TABS,
        value: 'a',
        onChange: () => {},
      }),
    )
    expect(html).toContain('ui-page-tabs__badge')
    expect(html).toContain('2') // badge value
  })

  it('active badge gets --active class', () => {
    const html = renderToStaticMarkup(
      createElement(PageTabs<Section>, {
        tabs: TABS,
        value: 'b', // Beta has the badge and is active
        onChange: () => {},
      }),
    )
    expect(html).toContain('ui-page-tabs__badge--active')
  })

  it('accepts an ariaLabel prop', () => {
    const html = renderToStaticMarkup(
      createElement(PageTabs<Section>, {
        tabs: TABS,
        value: 'a',
        onChange: () => {},
        ariaLabel: 'Custom label',
      }),
    )
    expect(html).toContain('aria-label="Custom label"')
  })
})

describe('PageTabs — keyboard navigation (DOM)', () => {
  // Clean up DOM between tests so getByText doesn't find elements from prior renders.
  afterEach(() => cleanup())

  it('ArrowRight calls onChange with the next tab', () => {
    const onChange = vi.fn()
    const { getByText } = render(
      createElement(PageTabs<Section>, { tabs: TABS, value: 'a', onChange }),
    )
    fireEvent.keyDown(getByText('Alpha'), { key: 'ArrowRight' })
    expect(onChange).toHaveBeenCalledWith('b')
  })

  it('ArrowLeft calls onChange with the previous tab (wraps)', () => {
    const onChange = vi.fn()
    const { getByText } = render(
      createElement(PageTabs<Section>, { tabs: TABS, value: 'a', onChange }),
    )
    // ArrowLeft from the first tab should wrap to the last.
    fireEvent.keyDown(getByText('Alpha'), { key: 'ArrowLeft' })
    expect(onChange).toHaveBeenCalledWith('c')
  })

  it('Home calls onChange with the first tab', () => {
    const onChange = vi.fn()
    const { getByText } = render(
      createElement(PageTabs<Section>, { tabs: TABS, value: 'c', onChange }),
    )
    fireEvent.keyDown(getByText('Gamma'), { key: 'Home' })
    expect(onChange).toHaveBeenCalledWith('a')
  })

  it('End calls onChange with the last tab', () => {
    const onChange = vi.fn()
    const { getByText } = render(
      createElement(PageTabs<Section>, { tabs: TABS, value: 'a', onChange }),
    )
    fireEvent.keyDown(getByText('Alpha'), { key: 'End' })
    expect(onChange).toHaveBeenCalledWith('c')
  })

  it('moves keyboard focus with the selection, so it never strands on a tabIndex -1 tab', () => {
    const { getByText } = render(
      createElement(PageTabs<Section>, { tabs: TABS, value: 'a', onChange: vi.fn() }),
    )
    getByText('Alpha').focus()
    fireEvent.keyDown(getByText('Alpha'), { key: 'ArrowRight' })
    expect(document.activeElement?.textContent).toContain('Beta')
    fireEvent.keyDown(getByText('Alpha'), { key: 'End' })
    expect(document.activeElement?.textContent).toBe('Gamma')
  })

  it('ignores other keys', () => {
    const onChange = vi.fn()
    const { getByText } = render(
      createElement(PageTabs<Section>, { tabs: TABS, value: 'a', onChange }),
    )
    fireEvent.keyDown(getByText('Alpha'), { key: 'Enter' })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('clicking a tab calls onChange with its key', () => {
    const onChange = vi.fn()
    const { getByText } = render(
      createElement(PageTabs<Section>, { tabs: TABS, value: 'a', onChange }),
    )
    fireEvent.click(getByText('Gamma'))
    expect(onChange).toHaveBeenCalledWith('c')
  })

  it('active tab button carries the --active class', () => {
    const { getByText } = render(
      createElement(PageTabs<Section>, { tabs: TABS, value: 'b', onChange: () => {} }),
    )
    const betaBtn = getByText('Beta').closest('button')
    expect(betaBtn?.className).toContain('ui-page-tabs__tab--active')
  })

  it('inactive tab buttons do not carry the --active class', () => {
    const { getByText } = render(
      createElement(PageTabs<Section>, { tabs: TABS, value: 'b', onChange: () => {} }),
    )
    const alphaBtn = getByText('Alpha').closest('button')
    expect(alphaBtn?.className).not.toContain('ui-page-tabs__tab--active')
  })
})
