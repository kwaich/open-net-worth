// plan/usePlanState.ts — server-backed store for Outlook scenarios + FIRE baseline.
// Endpoints: GET/POST/PUT/DELETE /api/plan/scenarios · GET/PUT /api/plan/baseline.
// Field keys are camelCase, stored verbatim — no transformation.
// Mutators do an optimistic local-state update first; on API failure the update is
// reverted and the error is logged (no throw), except addScenario which must return
// the server-assigned id and propagates failure to the caller.

import { useCallback, useRef, useState } from 'react'
import { authFetch } from '../../../api/client'
import { setCachedQueryData, useQueryCache } from '../../../state/queryCache'

const SCENARIOS_URL = '/api/plan/scenarios'
const BASELINE_URL = '/api/plan/baseline'

export interface LifeEvent {
  id: string
  label: string
  amount: number
  age: number
}

export interface IncomeStream {
  label: string
  amount: number
  age: number
  endAge?: number | null
}

export interface ShockCfg {
  crashAge?: number | null
  weakFrom?: number | null
  weakTo?: number | null
  lostFrom?: number | null
  lostTo?: number | null
  inflFrom?: number | null
  inflTo?: number | null
}

export interface SavedScenario {
  id: string
  name: string
  successPct: number
  odds: number
  saving: number
  ret: number
  vol: number
  monthlyRetirementSpending: number
  swr: number | null
  lifeExp: number | null
  retireAge: number
  atNumber: boolean
  streams?: IncomeStream[]
  events?: LifeEvent[]
  crash: boolean
  weakSeq: boolean
  lostDec: boolean
  highInfl: boolean
  shockCfg?: ShockCfg
}

export interface BaseOverride {
  saving?: number | null
  ret?: number | null
  vol?: number | null
  monthlyRetirementSpending?: number | null
  swr?: number | null
  lifeExp?: number | null
  retireAge?: number | null
  streams?: IncomeStream[]
  events?: LifeEvent[]
}

export interface PlanStateResult {
  scenarios: SavedScenario[]
  baseline: BaseOverride | null
  loading: boolean
  /** Bumps on every successful refetch — consumers re-seed levers when it changes. */
  version: number
  /** Re-pull scenarios + baseline from the server (Lucius wrote plan state). */
  refetch(): Promise<void>
  addScenario(obj: Omit<SavedScenario, 'id'>): Promise<SavedScenario>
  updateScenario(id: string, obj: SavedScenario): Promise<void>
  deleteScenario(id: string): Promise<void>
  saveBaseline(obj: BaseOverride | null): Promise<void>
}

export function usePlanState(): PlanStateResult {
  const [version, setVersion] = useState(0)
  // Sequence guard (the useChat suggestSeq pattern): two refetches racing must never
  // let the SLOWER response overwrite the fresher one.
  const refetchSeq = useRef(0)

  const scenariosQuery = useQueryCache(
    SCENARIOS_URL,
    async () => {
      const response = await authFetch(SCENARIOS_URL)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return response.json() as Promise<{ scenarios: SavedScenario[] }>
    },
    { staleTime: 0 },
  )
  const baselineQuery = useQueryCache(
    BASELINE_URL,
    async () => {
      const response = await authFetch(BASELINE_URL)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return response.json() as Promise<{ baseline: BaseOverride | null }>
    },
    { staleTime: 0 },
  )
  const refreshScenarios = scenariosQuery.refresh
  const refreshBaseline = baselineQuery.refresh

  const refetch = useCallback(async (): Promise<void> => {
    const seq = ++refetchSeq.current
    try {
      await Promise.all([refreshScenarios(), refreshBaseline()])
      if (seq !== refetchSeq.current) return
      setVersion((v) => v + 1)
    } catch (e) {
      console.error('plan-state refetch failed', e)
    }
  }, [refreshBaseline, refreshScenarios])

  const addScenario = useCallback(
    async (obj: Omit<SavedScenario, 'id'>): Promise<SavedScenario> => {
      const res = await authFetch('/api/plan/scenarios', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(obj),
      })
      if (!res.ok) throw new Error(`POST /api/plan/scenarios → HTTP ${res.status}`)
      const created = (await res.json()) as SavedScenario
      setCachedQueryData<{ scenarios: SavedScenario[] }>(SCENARIOS_URL, (prev) => ({
        scenarios: [...(prev?.scenarios ?? []), created],
      }))
      return created
    },
    [],
  )

  const updateScenario = useCallback(async (id: string, obj: SavedScenario): Promise<void> => {
    let snap: SavedScenario[] = []
    setCachedQueryData<{ scenarios: SavedScenario[] }>(SCENARIOS_URL, (prev) => {
      snap = prev?.scenarios ?? []
      return { scenarios: snap.map((s) => (s.id === id ? obj : s)) }
    })
    const res = await authFetch(`/api/plan/scenarios/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(obj),
    })
    if (!res.ok) {
      setCachedQueryData(SCENARIOS_URL, { scenarios: snap })
      console.error(`PUT /api/plan/scenarios/${id} → HTTP ${res.status}`)
    }
  }, [])

  const deleteScenario = useCallback(async (id: string): Promise<void> => {
    let snap: SavedScenario[] = []
    setCachedQueryData<{ scenarios: SavedScenario[] }>(SCENARIOS_URL, (prev) => {
      snap = prev?.scenarios ?? []
      return { scenarios: snap.filter((s) => s.id !== id) }
    })
    const res = await authFetch(`/api/plan/scenarios/${id}`, { method: 'DELETE' })
    if (!res.ok) {
      setCachedQueryData(SCENARIOS_URL, { scenarios: snap })
      console.error(`DELETE /api/plan/scenarios/${id} → HTTP ${res.status}`)
    }
  }, [])

  const saveBaseline = useCallback(async (obj: BaseOverride | null): Promise<void> => {
    let snap: BaseOverride | null = null
    setCachedQueryData<{ baseline: BaseOverride | null }>(BASELINE_URL, (prev) => {
      snap = prev?.baseline ?? null
      return { baseline: obj }
    })
    try {
      const res = await authFetch('/api/plan/baseline', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(obj ?? {}),
      })
      if (!res.ok) throw new Error(`PUT /api/plan/baseline → HTTP ${res.status}`)
    } catch (error) {
      setCachedQueryData(BASELINE_URL, { baseline: snap })
      throw error
    }
  }, [])

  return {
    scenarios: scenariosQuery.data?.scenarios ?? [],
    baseline: baselineQuery.data?.baseline ?? null,
    loading: scenariosQuery.initialLoading || baselineQuery.initialLoading,
    version,
    refetch,
    addScenario,
    updateScenario,
    deleteScenario,
    saveBaseline,
  }
}
