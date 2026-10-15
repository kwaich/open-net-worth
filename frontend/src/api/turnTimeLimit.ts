// How long one Lucius turn (a chat reply or an Automation run) may run before it is stopped.
// GET/PUT /api/model-config/turn-time-limit — `seconds` 0 means no limit.

import { setCachedQueryData } from '../state/queryCache'
import { authFetch } from './client'

export const TURN_TIME_LIMIT_KEY = '/api/model-config/turn-time-limit'

export interface TurnTimeLimit {
  seconds: number
  default_seconds: number
  choices: number[]
}

export async function getTurnTimeLimit(): Promise<TurnTimeLimit> {
  const r = await authFetch(TURN_TIME_LIMIT_KEY)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json() as Promise<TurnTimeLimit>
}

export async function setTurnTimeLimit(seconds: number): Promise<TurnTimeLimit> {
  const r = await authFetch(TURN_TIME_LIMIT_KEY, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ seconds }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return setCachedQueryData(TURN_TIME_LIMIT_KEY, (await r.json()) as TurnTimeLimit)
}

export function turnTimeLimitLabel(seconds: number, defaultSeconds?: number): string {
  if (seconds <= 0) return 'No limit'
  const minutes = Math.round(seconds / 60)
  const text =
    minutes >= 60 && minutes % 60 === 0
      ? `${minutes / 60} hour${minutes === 60 ? '' : 's'}`
      : `${minutes} minute${minutes === 1 ? '' : 's'}`
  return seconds === defaultSeconds ? `${text} (default)` : text
}
