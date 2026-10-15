// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GridCard } from './GridCard'

const overviewCss = readFileSync('src/components/tabs/overview/overview.css', 'utf8')

afterEach(cleanup)

describe('GridCard mobile actions', () => {
  it('cancels native selection when a customize-mode card drag starts', () => {
    const onDragPointerDown = vi.fn()

    const { container } = render(
      <GridCard x={0} y={0} w={12} h={200} editing onDragPointerDown={onDragPointerDown}>
        Widget content
      </GridCard>,
    )

    const card = container.querySelector<HTMLElement>('.ov-griditem')!
    const pointerDown = new Event('pointerdown', { bubbles: true, cancelable: true })
    card.dispatchEvent(pointerDown)

    expect(pointerDown.defaultPrevented).toBe(true)
    expect(onDragPointerDown).toHaveBeenCalledOnce()
  })

  it('keeps mobile widget controls anchored to their own card', () => {
    const mobileRules = overviewCss.slice(overviewCss.indexOf('@media (max-width: 640px)'))

    expect(mobileRules).toMatch(/\.ov-griditem\s*\{[^}]*position:\s*relative\s*!important/s)
  })

  it('never renders a floating quick-action cluster in view mode', () => {
    const { container } = render(
      <GridCard x={0} y={0} w={12} h={200} editing={false} onOpenFullscreen={vi.fn()}>
        Widget content
      </GridCard>,
    )

    expect(container.querySelector('.ov-card-quick')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Full screen' })).toBeNull()
    expect(overviewCss).not.toContain('.ov-card-quick')
  })

  it('shows the widget-header menu on every screen size, roomier on touch', () => {
    expect(overviewCss).toMatch(/\.ov-widget-header-kebab \{\n {2}display: inline-flex;/)
    expect(overviewCss).not.toMatch(/\.ov-widget-header-kebab\s*\{\s*display:\s*none/)
    const coarse = overviewCss.slice(overviewCss.indexOf('@media (pointer: coarse)'))
    expect(coarse).toMatch(/\.ov-widget-header-kebab \.kebab-trigger\s*\{[^}]*width: 36px/s)
  })

  it('keeps the Customize toolbar visible without hover', () => {
    const rule = overviewCss.slice(overviewCss.indexOf('.ov-w-ctrls {'))
    const body = rule.slice(0, rule.indexOf('}'))
    expect(body).not.toMatch(/opacity:\s*0/)
    expect(body).not.toMatch(/pointer-events:\s*none/)
    expect(overviewCss).not.toMatch(/\.ov-griditem:hover \.ov-w-ctrls/)
  })

  // Regression for the "only the first widget's floating buttons respond"
  // bug: .ov-griditem going `position: static` on mobile left every
  // position:absolute floating control (quick-actions, Customize row, move
  // buttons) anchored to .ov-grid instead of its own card, so they all
  // stacked on top of each other. `position: relative` keeps the same
  // static-like layout but restores each card as its own containing block.
  it('keeps each mobile card as its own positioning context (not static)', () => {
    const mobileBlockStart = overviewCss.indexOf('@media (max-width: 640px) {')
    const mobileBlockEnd = overviewCss.indexOf('\n}\n', mobileBlockStart)
    const mobileBlock = overviewCss.slice(mobileBlockStart, mobileBlockEnd)
    const griditemRule = mobileBlock.slice(mobileBlock.indexOf('.ov-griditem {'))

    expect(griditemRule).toContain('position: relative !important')
    expect(griditemRule).not.toMatch(/position:\s*static/)
  })
})
