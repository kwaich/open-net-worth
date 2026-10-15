/**
 * Toast.tsx — Fixed bottom toast notification.
 * Ported from prototype/accounts/app.jsx Toast.
 * Auto-dismisses after 3 s; calls onDone when done.
 */

import { useEffect, useRef, type CSSProperties } from 'react'

export interface ToastProps {
  /** null or '' = hidden */
  message: string | null
  onDone?: () => void
  /** Where to centre. `'register'` centres over the transaction TABLE — clearing the nav rail,
   *  the accounts sidebar and the Lucius panel, exactly like the bulk bar above it.
   *  `'viewport'` (default) centres on the whole window, which is what a full-screen overlay
   *  such as the import modal wants. */
  anchor?: 'viewport' | 'register'
  /** Accounts sidebar width in px, or 0 when it is collapsed. Read only when
   *  `anchor='register'`; falls back to the CSS default width when omitted. */
  railWidth?: number
}

export function Toast({ message, onDone, anchor = 'viewport', railWidth }: ToastProps) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!message) return
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      onDone?.()
    }, 3000)
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [message])

  if (!message) return null

  return (
    <div
      className={'acct-toast mb-anchored' + (anchor === 'register' ? ' anchor-reg' : '')}
      style={
        anchor === 'register' && railWidth !== undefined
          ? ({ '--acct-toast-rail-w': `${railWidth}px` } as CSSProperties)
          : undefined
      }
      role="status"
      aria-live="polite"
    >
      {message}
    </div>
  )
}
