import { useCallback, useRef, useState, type RefObject } from 'react'

/** A form draft whose newest value an event handler can read before React re-renders. A
 *  MoneyField commits its number on Enter and then fires onKeyDown in the same event — a
 *  keyboard submit reading rendered state would still see the value from before the commit,
 *  so it reads `latest.current` instead. Render from `draft` as usual. */
export function useSyncedDraft<T extends object>(
  initial: T | (() => T),
): readonly [T, (patch: Partial<T>) => void, RefObject<T>] {
  const [draft, setDraft] = useState(initial)
  const latest = useRef(draft)
  const update = useCallback((patch: Partial<T>) => {
    latest.current = { ...latest.current, ...patch }
    setDraft(latest.current)
  }, [])
  return [draft, update, latest] as const
}
