// @vitest-environment jsdom
//
// Regression test for the "i" bubble getting stuck open on phones. iOS Safari replays a
// synthetic mouseenter/mouseleave sequence after a tap, but only a real mouse produces the
// matching mouseleave — a tap never does. InfoPopover used to listen on mouseenter/mouseleave
// directly, so a tap latched `revealed` true forever and the bubble never closed even after the
// scrim tap cleared `pinned`. Pointer events carry a real `pointerType`, so gating on
// `pointerType === 'mouse'` is what actually distinguishes a tap from a hover.

import { describe, it, expect, afterEach } from 'vitest'
import { render, fireEvent, cleanup, screen } from '@testing-library/react'
import { InfoPopover } from './InfoPopover'

afterEach(() => cleanup())

describe('InfoPopover — touch vs. mouse', () => {
  it('an outside tap fully closes a focused, pinned bubble', () => {
    render(<InfoPopover text="Some info" />)
    const button = screen.getByRole('button', { name: 'More information' })

    fireEvent.focus(button)
    fireEvent.click(button)
    expect(screen.getByRole('tooltip')).toBeTruthy()

    const scrim = document.querySelector('.info-pop-scrim') as HTMLElement
    // Account editor sheets intentionally own touch gestures (`touch-action: none`), which can
    // suppress the synthetic click that normally follows a touch. The dismiss layer must close
    // on the actual intercepted pointer event instead.
    fireEvent.pointerDown(scrim, { pointerType: 'touch' })
    expect(screen.queryByRole('tooltip')).toBeNull()
  })

  it('Escape closes a pinned bubble', () => {
    render(<InfoPopover text="Some info" />)
    const button = screen.getByRole('button', { name: 'More information' })

    fireEvent.click(button)
    expect(screen.getByRole('tooltip')).toBeTruthy()
    fireEvent.keyDown(button, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
  })

  it('a real mouse still reveals on hover and hides on leave, no click required', () => {
    render(<InfoPopover text="Some info" />)
    const wrap = document.querySelector('.info-pop-wrap') as HTMLElement

    fireEvent.pointerEnter(wrap, { pointerType: 'mouse' })
    expect(screen.getByRole('tooltip')).toBeTruthy()

    fireEvent.pointerLeave(wrap, { pointerType: 'mouse' })
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})
