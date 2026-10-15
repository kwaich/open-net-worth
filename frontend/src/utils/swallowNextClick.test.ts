// @vitest-environment jsdom
//
// The press that closes a phone sheet from its backdrop must not also click whatever lies beneath
// (owner: "when tapping outside to dismiss the modal, the click goes through and triggers
// others") — and must never eat a later, separate click.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { swallowNextClick } from './swallowNextClick'

let row: HTMLButtonElement
let opened: ReturnType<typeof vi.fn>

/** A tap's click, as a phone delivers it (`detail` 1). */
function tapClick(el: Element) {
  const event = new MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 })
  el.dispatchEvent(event)
  return event
}

beforeEach(() => {
  opened = vi.fn()
  row = document.createElement('button')
  row.addEventListener('click', opened)
  document.body.append(row)
})

afterEach(() => {
  row.remove()
  window.dispatchEvent(new Event('pointerdown')) // lapse a guard a test left armed
})

describe('swallowNextClick', () => {
  it('eats the click that ends the press, wherever it lands — and only that one', () => {
    swallowNextClick()
    expect(tapClick(row).defaultPrevented).toBe(true)
    expect(opened).not.toHaveBeenCalled()
    tapClick(row)
    expect(opened).toHaveBeenCalledOnce()
  })

  it('lapses at the next press, so a tap that never became a click cannot eat the next one', () => {
    swallowNextClick()
    row.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    tapClick(row)
    expect(opened).toHaveBeenCalledOnce()
  })

  it('never eats a keyboard or scripted click', () => {
    swallowNextClick()
    row.click()
    expect(opened).toHaveBeenCalledOnce()
  })
})
