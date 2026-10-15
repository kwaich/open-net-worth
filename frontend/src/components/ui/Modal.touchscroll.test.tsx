// @vitest-environment jsdom
/**
 * Touch scrolling on exempt surfaces while a modal is open.
 *
 * react-remove-scroll registers a document-level touchmove listener from a passive effect
 * inside Dialog.Content and cancels every touch outside the card. Modal registers its own
 * listener from a LAYOUT effect, so it runs first and can stop the library's for exempt
 * targets only (the transaction drawer, the Lucius surfaces). This test stands in for the
 * library with a listener added AFTER the modal mounts and checks which touches reach it.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { Modal } from './Modal'

afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
})

function touchMove(target: Element): void {
  target.dispatchEvent(new Event('touchmove', { bubbles: true, cancelable: true }))
}

describe('Modal — touch scrolling on exempt surfaces', () => {
  it('stops a later document touchmove listener for an exempt surface, not for other outsiders', () => {
    const drawer = document.createElement('div')
    drawer.className = 'acct-detail-wrap'
    const drawerInner = document.createElement('div')
    drawer.appendChild(drawerInner)
    const bystander = document.createElement('div')
    bystander.className = 'somewhere-else'
    document.body.append(drawer, bystander)

    render(
      <Modal open onClose={vi.fn()} title="Review transactions">
        <button>Inside</button>
      </Modal>,
    )
    const library = vi.fn()
    document.addEventListener('touchmove', library)

    touchMove(drawerInner)
    expect(library).not.toHaveBeenCalled()

    touchMove(bystander)
    expect(library).toHaveBeenCalledTimes(1)

    document.removeEventListener('touchmove', library)
  })

  it('does nothing once the modal is closed', () => {
    const drawer = document.createElement('div')
    drawer.className = 'acct-detail-wrap'
    document.body.append(drawer)
    const { rerender } = render(
      <Modal open onClose={vi.fn()} title="Review transactions">
        <button>Inside</button>
      </Modal>,
    )
    rerender(
      <Modal open={false} onClose={vi.fn()} title="Review transactions">
        <button>Inside</button>
      </Modal>,
    )
    const library = vi.fn()
    document.addEventListener('touchmove', library)
    touchMove(drawer)
    expect(library).toHaveBeenCalledTimes(1)
    document.removeEventListener('touchmove', library)
  })
})
