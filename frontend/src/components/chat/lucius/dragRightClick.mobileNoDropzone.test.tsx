// @vitest-environment jsdom
//
// Below the phone breakpoint, ChatPanel/LuciusDock (the only two [data-dropzone] targets in
// the app) aren't rendered at all — Lucius docks as an ordinary tab instead. EntityRow still
// stamps draggable="true" unconditionally on every row (its own file may not import ONW-domain
// hooks like useIsMobile), so a real drag gesture there previously still built and showed the
// "● TYPE label" ghost chip and lifted the row — a drag experience with nowhere it could ever
// land. dragstart's onStart handler now calls preventDefault() and bails immediately when no
// [data-dropzone] exists anywhere in the document, regardless of why (mobile viewport, or any
// other reason one isn't currently mounted) — this is the single global choke point, not a
// per-EntityRow-consumer fix. preventDefault() matters, not just an early return: it's the
// spec's actual mechanism for cancelling the native HTML5 drag outright, so no default browser
// drag-image appears either — a bare early return would only skip our OWN ghost/dataTransfer
// logic while the native drag still silently proceeded.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { DragRightClick } from './dragRightClick'

afterEach(() => cleanup())

vi.mock('../../../state/chatComposer', () => ({
  useChatComposer: () => ({ addMention: vi.fn() }),
}))

function makeDraggableRow() {
  const el = document.createElement('div')
  el.setAttribute('data-entity-id', 'transaction:t1')
  el.setAttribute('data-entity-label', 'Vertex Wireless')
  el.setAttribute('draggable', 'true')
  document.body.appendChild(el)
  return el
}

describe('dragRightClick — no [data-dropzone] means dragstart never starts a drag', () => {
  it('with no dropzone anywhere (the mobile case): dragstart is cancelled outright — no lift, no ghost, no native drag', () => {
    render(<DragRightClick />)
    const el = makeDraggableRow()

    // fireEvent.X returns dispatchEvent's own result: false iff the event was cancelable AND
    // preventDefault() was called — the real signal a browser uses to abort the native drag.
    const notCancelled = fireEvent.dragStart(el, { dataTransfer: undefined })

    expect(notCancelled).toBe(false)
    expect(el.classList.contains('dragging')).toBe(false)
    expect(document.querySelector('.mx-drag-chip')).toBeNull()

    el.remove()
  })

  it('with a dropzone present (the desktop case): dragstart still lifts the row normally, uncancelled', () => {
    render(<DragRightClick />)
    const el = makeDraggableRow()
    const dropzone = document.createElement('div')
    dropzone.setAttribute('data-dropzone', 'side')
    document.body.appendChild(dropzone)

    const notCancelled = fireEvent.dragStart(el, { dataTransfer: undefined })

    expect(notCancelled).toBe(true)
    expect(el.classList.contains('dragging')).toBe(true)

    el.remove()
    dropzone.remove()
  })
})
