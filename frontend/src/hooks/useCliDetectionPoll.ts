import { useCallback, useEffect, useRef, useState } from 'react'

export const CLI_DETECTION_INTERVAL = 5000

export function useCliDetectionPoll(refresh: () => Promise<unknown>, enabled: boolean) {
  const latest = useRef(refresh)
  const pending = useRef<Promise<unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    latest.current = refresh
  }, [refresh])

  const check = useCallback(() => {
    if (pending.current) return pending.current
    const request = Promise.resolve()
      .then(() => latest.current())
      .then(
        (value) => {
          setError(null)
          return value
        },
        (failure: unknown) => {
          setError('Could not check installations. Check again.')
          throw failure
        },
      )
      .finally(() => {
        pending.current = null
      })
    pending.current = request
    return request
  }, [])

  useEffect(() => {
    if (!enabled) return
    const refreshVisible = () => {
      if (document.visibilityState !== 'hidden') void check().catch(() => {})
    }
    const timer = window.setInterval(refreshVisible, CLI_DETECTION_INTERVAL)
    window.addEventListener('focus', refreshVisible)
    document.addEventListener('visibilitychange', refreshVisible)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', refreshVisible)
      document.removeEventListener('visibilitychange', refreshVisible)
    }
  }, [enabled, check])

  return { check, error }
}
