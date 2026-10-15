// @vitest-environment jsdom
//
// Owner reports (mobile Lucius, three rounds): the picker opened BEHIND the keyboard, then
// one row too LOW, covering the text being typed. The popup measured the composer once on
// open and again only on window / visualViewport events — but the composer keeps moving
// AFTER those fire while the keyboard and its pinned layout settle, so every event-time rect
// was mid-travel. The popup now tracks the composer's rect every animation frame while open.

import { createRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import { Autocomplete } from './Autocomplete'
import type { UseMentionablesResult } from '../../../hooks/useMentionables'

afterEach(cleanup)
Element.prototype.scrollIntoView = vi.fn()

vi.mock('../../../hooks/useMentionables', () => ({
  useMentionables: (): UseMentionablesResult => ({
    items: [],
    loading: false,
    error: false,
    retry: vi.fn(),
    remoteItems: [],
  }),
}))
vi.mock('../../../lucius/screenContract', () => ({ allHandles: () => [] }))
vi.mock('../../../lucius/identity', () => ({ Spark: () => null, Identity: () => null }))

function anchorAt(top: number): {
  ref: React.RefObject<HTMLElement | null>
  move(top: number): void
} {
  const el = document.createElement('div')
  document.body.appendChild(el)
  let current = top
  el.getBoundingClientRect = () =>
    ({
      top: current,
      bottom: current + 60,
      left: 10,
      right: 390,
      width: 380,
      height: 60,
    }) as DOMRect
  const ref = createRef<HTMLElement | null>()
  ref.current = el
  return {
    ref,
    move(next: number) {
      current = next
    },
  }
}

/** Two frames: one for the tracker to read the moved rect, one for React to commit it. */
async function frames() {
  await act(async () => {
    await new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r())))
  })
}

describe('Autocomplete — follows the composer wherever it settles', () => {
  it('tracks the anchor every frame — no event needed when the composer moves late', async () => {
    // jsdom has no layout: give the popup its CSS height (lucius.css `.ac { height: 320px }`).
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
      value: 320,
      configurable: true,
    })
    const anchor = anchorAt(800)

    render(
      <Autocomplete
        mode="@"
        query=""
        visibleIds={new Set()}
        onPick={vi.fn()}
        onClose={vi.fn()}
        anchorRef={anchor.ref}
      />,
    )
    const popup = document.querySelector<HTMLElement>('.ac')!
    // Above the anchor: its top, minus the 6px gap, minus the popup's own height.
    expect(popup.style.top).toBe('474px')

    // The keyboard finishes sliding up two frames later, with NO resize/scroll event.
    anchor.move(500)
    await frames()

    expect(popup.style.top).toBe('174px')
  })
})
