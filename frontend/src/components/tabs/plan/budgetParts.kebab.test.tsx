// @vitest-environment jsdom
//
// Kebab / ColorDot portal fix: both popovers are position:fixed,
// which used to be computed IN PLACE — fine everywhere except inside a modal whose own overlay
// carries backdrop-filter (CategoriesManager's .pl-ov), which becomes a containing block for
// fixed descendants and mispositions them exactly like the delete modal did before 2af27c14.
// Portaling to '.app-shell' fixes the position; this file pins the OTHER half of that fix — the
// outside-click dismiss must check BOTH the trigger ref and the now-detached portaled panel, or
// every click on a menu item would register as "outside" and close the menu before its own
// onClick fires.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { Kebab, ColorDot } from './budgetParts'

afterEach(() => cleanup())

describe('Kebab — portaled menu', () => {
  it("opens the menu and renders it OUTSIDE the trigger's own DOM subtree (portaled)", () => {
    const { container } = render(
      <Kebab label="Row options" items={[{ label: 'Do the thing', onClick: vi.fn() }]} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Row options' }))
    const item = screen.getByText('Do the thing')
    expect(item).toBeTruthy()
    // The portaled node is NOT a descendant of the component's own rendered container.
    expect(container.contains(item)).toBe(false)
  })

  it('clicking a menu item fires its onClick (mousedown-outside must not have pre-empted it)', () => {
    const onClick = vi.fn()
    render(<Kebab label="Row options" items={[{ label: 'Do the thing', onClick }]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Row options' }))
    fireEvent.click(screen.getByText('Do the thing'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('a mousedown squarely inside the portaled panel does not close the menu', () => {
    render(
      <Kebab
        label="Row options"
        items={[
          { label: 'Do the thing', onClick: vi.fn() },
          { label: 'Another', onClick: vi.fn() },
        ]}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Row options' }))
    const item = screen.getByText('Do the thing')
    fireEvent.mouseDown(item)
    // Still open — the dual-ref inside() check recognized the portaled panel as "inside".
    expect(screen.getByText('Another')).toBeTruthy()
  })

  it('a mousedown truly outside (document.body) closes the menu', () => {
    render(<Kebab label="Row options" items={[{ label: 'Do the thing', onClick: vi.fn() }]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Row options' }))
    expect(screen.getByText('Do the thing')).toBeTruthy()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByText('Do the thing')).toBeNull()
  })
})

describe('ColorDot — portaled popover', () => {
  it("opens the swatch grid, portaled outside the trigger's own subtree", () => {
    const { container } = render(
      <ColorDot color="#34b08a" onPick={vi.fn()} label="Color for Groceries" />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Color for Groceries' }))
    const swatch = screen.getByRole('button', { name: '#3b86dd' })
    expect(swatch).toBeTruthy()
    expect(container.contains(swatch)).toBe(false)
  })

  it('picking a swatch fires onPick and a click inside the panel does not dismiss it first', () => {
    const onPick = vi.fn()
    render(<ColorDot color="#34b08a" onPick={onPick} label="Color for Groceries" />)
    fireEvent.click(screen.getByRole('button', { name: 'Color for Groceries' }))
    fireEvent.click(screen.getByRole('button', { name: '#3b86dd' }))
    expect(onPick).toHaveBeenCalledWith('#3b86dd')
  })
})
