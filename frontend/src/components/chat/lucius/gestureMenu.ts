// gestureMenu.ts — the two gestures that open the "Ask Lucius about this" menu on a
// [data-entity-id] row: desktop right-click (contextmenu) and touch long-press (RFC §2.2,
// MB5). One hook, one piece of press state, because the two are NOT independent on a phone:
//
//   • Android Chrome fires its own `contextmenu` for a long-press at about the same 500ms our
//     timer uses. Whichever lands first opens the menu at the touch position and cancels the
//     other — never two placements.
//   • iOS starts a text selection at the same moment — cleared when we open (the phone
//     stylesheet also sets user-select/touch-callout: none on rows, index.css).
//   • iOS synthesizes a `click` on lift-off — swallowed once (capture phase) so the row's own
//     onClick doesn't open its detail screen underneath the menu. Disarmed by the next
//     touchstart or a short timeout, so a browser that never synthesizes that click can't
//     have its next real tap eaten.
//
// The pressed row wears `.mb-pressed` while held; any scroll/move cancels cleanly (MB5.3).

import { useEffect } from 'react'

const LONG_PRESS_MS = 500
// How far the finger may drift before a press is a scroll, not a long-press.
const MOVE_TOLERANCE_PX = 10
// Touch placement: above the finger so the menu isn't under it.
const TOUCH_MENU_LIFT_PX = 56
// How long after lift-off the synthesized click is still expected (and swallowed).
const SWALLOW_CLICK_MS = 700
// How long after a touch-opened menu a late `contextmenu` for the same press is ignored.
const TOUCH_CTX_GRACE_MS = 1000

export type OpenGestureMenu = (el: Element, x: number, y: number) => void

interface Press {
  el: Element
  x: number
  y: number
  timer: ReturnType<typeof setTimeout>
}

function rowFor(target: EventTarget | null): Element | null {
  const el = (target as Element | null)?.closest?.('[data-entity-id]') ?? null
  return el && !el.closest('[data-ctx-ignore]') ? el : null
}

export function useGestureMenu(open: OpenGestureMenu) {
  useEffect(() => {
    let press: Press | null = null
    let touchOpenedAt = 0
    let swallowNextClick = false
    let disarm: ReturnType<typeof setTimeout> | null = null

    const cancelPress = () => {
      if (!press) return
      clearTimeout(press.timer)
      press.el.classList.remove('mb-pressed')
      press = null
    }

    // Shared by both gestures: open above the finger, drop any selection that raced in.
    const openFromTouch = (p: Press) => {
      cancelPress()
      window.getSelection?.()?.removeAllRanges()
      swallowNextClick = true
      touchOpenedAt = Date.now()
      open(p.el, p.x, Math.max(8, p.y - TOUCH_MENU_LIFT_PX))
    }

    const onCtx = (e: MouseEvent) => {
      const el = rowFor(e.target)
      if (!el) return
      e.preventDefault()
      // Our timer may land first (late native contextmenu → ignore) or second (press still
      // pending → open now from the touch state and cancel the timer). Either way: one menu.
      if (press) {
        openFromTouch(press)
        return
      }
      if (Date.now() - touchOpenedAt < TOUCH_CTX_GRACE_MS) return
      open(el, e.clientX, e.clientY)
    }

    const onTouchStart = (e: TouchEvent) => {
      swallowNextClick = false
      if (disarm) clearTimeout(disarm)
      cancelPress()
      const touch = e.touches[0]
      if (!touch || e.touches.length > 1) return
      const el = rowFor(e.target)
      if (!el) return
      el.classList.add('mb-pressed')
      const timer = setTimeout(() => {
        if (press) openFromTouch(press)
      }, LONG_PRESS_MS)
      press = { el, x: touch.clientX, y: touch.clientY, timer }
    }

    const onTouchMove = (e: TouchEvent) => {
      const touch = e.touches[0]
      if (!touch || !press) return
      if (
        Math.abs(touch.clientX - press.x) > MOVE_TOLERANCE_PX ||
        Math.abs(touch.clientY - press.y) > MOVE_TOLERANCE_PX
      ) {
        cancelPress()
      }
    }

    const onTouchEnd = () => {
      cancelPress()
      if (!swallowNextClick) return
      if (disarm) clearTimeout(disarm)
      disarm = setTimeout(() => {
        swallowNextClick = false
      }, SWALLOW_CLICK_MS)
    }

    const onClick = (e: MouseEvent) => {
      if (!swallowNextClick) return
      swallowNextClick = false
      e.preventDefault()
      e.stopPropagation()
    }

    document.addEventListener('contextmenu', onCtx)
    document.addEventListener('touchstart', onTouchStart, { passive: true })
    document.addEventListener('touchmove', onTouchMove, { passive: true })
    document.addEventListener('touchend', onTouchEnd)
    document.addEventListener('touchcancel', onTouchEnd)
    document.addEventListener('click', onClick, true)
    return () => {
      cancelPress()
      if (disarm) clearTimeout(disarm)
      document.removeEventListener('contextmenu', onCtx)
      document.removeEventListener('touchstart', onTouchStart)
      document.removeEventListener('touchmove', onTouchMove)
      document.removeEventListener('touchend', onTouchEnd)
      document.removeEventListener('touchcancel', onTouchEnd)
      document.removeEventListener('click', onClick, true)
    }
  }, [open])
}
