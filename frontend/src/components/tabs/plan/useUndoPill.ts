// plan/useUndoPill.ts — the Budget's one-shot, SERVER-BACKED Undo (handoff delta #11), shared by
// Zero-Based (useBudgetModel) and 50/30/20 (PropTable). The pill's inverse is a real closure that
// replays the exact inverse write(s) against the API — never a client-state snapshot, which is
// a lie after any refetch. 7s, one at a time: a fresh capture clears any pending timer/pill.
import { useCallback, useEffect, useRef, useState } from 'react'

export function useUndoPill() {
  const [undo, setUndo] = useState<{ label: string } | null>(null)
  const inverseRef = useRef<(() => Promise<void>) | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const captureUndo = useCallback((label: string, inverse: () => Promise<void>) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    inverseRef.current = inverse
    setUndo({ label })
    timerRef.current = setTimeout(() => {
      setUndo(null)
      inverseRef.current = null
    }, 7000)
  }, [])

  const doUndo = useCallback((toast: (msg: string) => void) => {
    const inverse = inverseRef.current
    if (!inverse) return
    if (timerRef.current) clearTimeout(timerRef.current)
    inverseRef.current = null
    setUndo(null)
    inverse()
      .then(() => toast('Undone'))
      .catch((err) => toast(err instanceof Error && err.message ? err.message : 'Undo failed'))
  }, [])

  /** Drop the pill without running it — e.g. on a month change, which its inverse's trailing
   *  refresh would otherwise repaint with the captured (now off-screen) month (FEB-08). */
  const clearUndo = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    inverseRef.current = null
    setUndo(null)
  }, [])

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    },
    [],
  )

  return { undo, captureUndo, doUndo, clearUndo }
}
