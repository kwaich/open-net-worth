// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { _resetOvTooltipForTests, installOvTooltip } from './tooltip'

afterEach(() => {
  _resetOvTooltipForTests()
  document.querySelectorAll('[data-tip]').forEach((n) => n.remove())
})

function hover(tip: string, color: string): HTMLElement {
  installOvTooltip()
  const target = document.createElement('div')
  target.setAttribute('data-tip', tip)
  target.setAttribute('data-tip-color', color)
  document.body.appendChild(target)
  target.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 10, clientY: 10 }))
  return document.querySelector('.ov-htip') as HTMLElement
}

describe('overview tooltip', () => {
  it('never turns a hostile data-tip-color into markup', () => {
    const tip = hover('Food\n$12', '"><img src=x onerror=alert(1)>')
    expect(tip.querySelector('img')).toBeNull()
    expect(tip.querySelector('.ov-htip-dot')).toBeNull()
    expect(tip.textContent).toContain('Food')
  })

  it('escapes tooltip text and keeps a valid colour dot', () => {
    const tip = hover('<b>Food</b>\n$12', '#3b86dd')
    expect(tip.querySelector('b')).toBeNull()
    expect(tip.querySelector('.ov-htip-dot')).not.toBeNull()
  })
})
