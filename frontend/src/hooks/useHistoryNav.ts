import { useCallback, useEffect, useReducer, useState } from 'react'
import { useRouter } from '@tanstack/react-router'
import type { RouterHistory } from '@tanstack/history'

type Action = 'PUSH' | 'REPLACE' | 'FORWARD' | 'BACK' | 'GO'

// Back/forward availability for the app's own router history. The History API never reveals
// whether a forward entry exists, so the furthest index reached is tracked: a PUSH discards
// everything ahead of it, BACK/FORWARD/GO only move within what was already reached.
export class HistoryReach {
  private furthest: number
  readonly history: RouterHistory
  constructor(history: RouterHistory) {
    this.history = history
    this.furthest = this.index
  }
  get index(): number {
    return this.history.location.state.__TSR_index
  }
  get canGoBack(): boolean {
    return this.history.canGoBack()
  }
  get canGoForward(): boolean {
    return this.index < this.furthest
  }
  observe(action: Action): void {
    this.furthest = action === 'PUSH' ? this.index : Math.max(this.furthest, this.index)
  }
}

export function useHistoryNav() {
  const { history } = useRouter()
  const [reach] = useState(() => new HistoryReach(history))
  const [, refresh] = useReducer((n: number) => n + 1, 0)
  useEffect(
    () =>
      history.subscribe(({ action }) => {
        reach.observe(action.type)
        refresh()
      }),
    [history, reach],
  )
  const back = useCallback(() => history.back(), [history])
  const forward = useCallback(() => history.forward(), [history])
  return { canGoBack: reach.canGoBack, canGoForward: reach.canGoForward, back, forward }
}
