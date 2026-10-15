import { useEffect, useState } from 'react'
import { fetchInvestmentActivity } from '../api/investments'
import type { ActivityFeed } from '../types/activity'

type Status = 'idle' | 'loading' | 'ok' | 'error'

interface State {
  data: ActivityFeed | null
  status: Status
}

// Fetches the investment activity feed for the last 12 months by default.
// Re-fetches whenever `version` changes (e.g. after a disposal records a new lot).
export function useInvestmentActivity(version = 0): State {
  const [state, setState] = useState<State>({ data: null, status: 'loading' })

  useEffect(() => {
    let alive = true
    setState({ data: null, status: 'loading' })
    const today = new Date()
    const from = new Date(today)
    from.setFullYear(from.getFullYear() - 1)
    const fmt = (d: Date) => d.toISOString().slice(0, 10)

    fetchInvestmentActivity({ date_from: fmt(from), date_to: fmt(today) })
      .then((data) => {
        if (!alive) return
        setState({ data, status: 'ok' })
      })
      .catch(() => {
        if (!alive) return
        setState({ data: null, status: 'error' })
      })

    return () => {
      alive = false
    }
  }, [version])

  return state
}
