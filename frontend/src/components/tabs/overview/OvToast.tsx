// Overview v2 — transient "widget added" toast (parity item 8). Mirrors the
// prototype's `showToast` (a single message + fixed bottom pill, auto-
// dismissing) and this app's own per-tab toast convention (no shared
// toast/notification component exists to reuse — `components/tabs/accounts/
// Toast.tsx` is the closest precedent and follows the identical shape).

import { useEffect, useRef } from 'react'

export interface OvToastProps {
  /** null = hidden */
  message: string | null
  onDone: () => void
}

const TOAST_MS = 3000

export function OvToast({ message, onDone }: OvToastProps) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Latest `onDone` in a ref, kept OUT of the effect's deps — a parent
  // re-render while a toast is showing (e.g. the auto-height effect patching
  // a widget's height right after it's added) passes a new closure every
  // time; depending on it directly restarted the 3s timer on every such
  // render, letting the toast outlive its intended window (review finding).
  const onDoneRef = useRef(onDone)
  onDoneRef.current = onDone

  useEffect(() => {
    if (!message) return
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => onDoneRef.current(), TOAST_MS)
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [message])

  if (!message) return null

  return (
    <div className="ov-toast mb-anchored" role="status" aria-live="polite">
      {message}
    </div>
  )
}
