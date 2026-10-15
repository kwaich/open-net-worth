import { useCallback, useEffect, useRef } from 'react'
import type { PointerEvent as ReactPointerEvent, PointerEventHandler } from 'react'

export interface PointerResizeOptions {
  onStart: (event: PointerEvent) => void
  onMove: (event: PointerEvent) => void
  onEnd: (event: PointerEvent) => void
}

interface ResizeState {
  pointerId: number
  target: HTMLElement
  userSelect: string
  move: (event: PointerEvent) => void
  finish: (event: PointerEvent) => void
}

export function usePointerResize({
  onStart,
  onMove,
  onEnd,
}: PointerResizeOptions): PointerEventHandler<HTMLElement> {
  const callbacks = useRef({ onStart, onMove, onEnd })
  const state = useRef<ResizeState | null>(null)

  useEffect(() => {
    callbacks.current = { onStart, onMove, onEnd }
  }, [onEnd, onMove, onStart])

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    if (state.current || event.isPrimary === false) return
    if (event.pointerType === 'mouse' && event.button !== 0) return
    const pointerId = event.pointerId
    const target = event.currentTarget
    const move = (moveEvent: PointerEvent) => {
      if (state.current?.pointerId !== moveEvent.pointerId) return
      callbacks.current.onMove(moveEvent)
    }
    const finish = (endEvent: PointerEvent) => {
      const active = state.current
      if (!active || active.pointerId !== endEvent.pointerId) return
      state.current = null
      active.target.removeEventListener('pointermove', active.move)
      active.target.removeEventListener('pointerup', active.finish)
      active.target.removeEventListener('pointercancel', active.finish)
      active.target.removeEventListener('lostpointercapture', active.finish)
      try {
        active.target.releasePointerCapture?.(active.pointerId)
      } catch {
        // Pointer capture can already be released by the browser when a pointer ends.
      }
      document.body.style.userSelect = active.userSelect
      callbacks.current.onEnd(endEvent)
    }
    state.current = { pointerId, target, userSelect: document.body.style.userSelect, move, finish }
    document.body.style.userSelect = 'none'
    target.addEventListener('pointermove', move)
    target.addEventListener('pointerup', finish)
    target.addEventListener('pointercancel', finish)
    target.addEventListener('lostpointercapture', finish)
    target.setPointerCapture?.(pointerId)
    callbacks.current.onStart(event.nativeEvent)
  }, [])

  useEffect(() => {
    return () => {
      const active = state.current
      if (!active) return
      state.current = null
      active.target.removeEventListener('pointermove', active.move)
      active.target.removeEventListener('pointerup', active.finish)
      active.target.removeEventListener('pointercancel', active.finish)
      active.target.removeEventListener('lostpointercapture', active.finish)
      document.body.style.userSelect = active.userSelect
    }
  }, [])

  return onPointerDown
}
