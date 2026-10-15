// useDataVersion — subscribe a component to Lucius data_changed domains. Returns a
// monotonic version that bumps when any watched domain (or '*') changes; put it in a
// useEffect dep list to refetch. One line per tab is the whole adoption story.
// Reads through `useQueryCache` need none of this — the cache revalidates on every write.
// Pass '*' to bump on ANY write: the default for a screen holding fetched data in local state.
import { useEffect, useRef, useState } from 'react'
import { onDataChanged } from '../lucius/bus'

export function useDataVersion(domains: string | string[]): number {
  const wanted = Array.isArray(domains) ? domains : [domains]
  const key = wanted.join(',')
  const wantedRef = useRef(wanted)
  wantedRef.current = wanted
  const [version, setVersion] = useState(0)
  useEffect(
    () =>
      onDataChanged((changed) => {
        if (
          changed.includes('*') ||
          wantedRef.current.includes('*') ||
          changed.some((d) => wantedRef.current.includes(d))
        )
          setVersion((v) => v + 1)
      }),
    [key],
  )
  return version
}
