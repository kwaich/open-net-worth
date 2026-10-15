import { useCallback, useState } from 'react'

/** A string state that survives a page refresh within the browser session (sessionStorage);
 *  a new session starts empty. `null` clears it. Storage failures (a private window with
 *  storage off) fall back to plain state. */
export function useSessionState(key: string): [string | null, (value: string | null) => void] {
  const [value, setValue] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(key)
    } catch {
      return null
    }
  })
  const set = useCallback(
    (next: string | null) => {
      setValue(next)
      try {
        if (next === null) sessionStorage.removeItem(key)
        else sessionStorage.setItem(key, next)
      } catch {
        /* storage unavailable — the state still works for this page */
      }
    },
    [key],
  )
  return [value, set]
}
