import { useCallback } from 'react'
import type { ClaudeSkillMetadata, OpenAISkillMetadata, Skill } from '../types/skills'
import { authFetch } from '../api/client'
import { invalidateQueryCache, useQueryCache } from '../state/queryCache'

export interface SkillCreateBody {
  name: string
  description: string
  body?: string
  claude?: ClaudeSkillMetadata
  openai?: OpenAISkillMetadata
}

export interface SkillsState {
  skills: Skill[]
  loading: boolean
  error: string | null
  refresh: () => void
  createSkill: (body: SkillCreateBody) => Promise<{ ok: boolean; error?: string }>
  updateSkill: (
    id: string,
    changes: Record<string, unknown>,
  ) => Promise<{ ok: boolean; error?: string }>
  deleteSkill: (id: string) => Promise<{ ok: boolean; error?: string }>
}

async function problem(r: Response): Promise<string> {
  // FastAPI HTTPException → { detail: "..." }; fall back to status text.
  try {
    const j = await r.json()
    if (typeof j?.detail === 'string') return j.detail
    if (Array.isArray(j?.detail)) return j.detail.map((d: { msg?: string }) => d.msg).join('; ')
  } catch {
    /* not JSON */
  }
  return `Request failed (${r.status})`
}

/** Fetches /api/skills and exposes the direct (non-LLM) authoring path. */
export function useSkills(): SkillsState {
  const query = useQueryCache<unknown>('tab:skills', async () => {
    const response = await authFetch('/api/skills')
    if (!response.ok) throw new Error(await problem(response))
    const payload: unknown = await response.json()
    if (!Array.isArray(payload)) throw new Error('The server returned an invalid skills list.')
    return payload
  })
  const load = useCallback(async () => {
    await query.refresh()
  }, [query])

  const createSkill = useCallback(
    async (body: SkillCreateBody) => {
      try {
        const r = await authFetch('/api/skills', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
        if (!r.ok) return { ok: false, error: await problem(r) }
        invalidateQueryCache('tab:skills')
        await load()
        return { ok: true }
      } catch {
        return { ok: false, error: 'Could not reach the server.' }
      }
    },
    [load],
  )

  const updateSkill = useCallback(
    async (id: string, changes: Record<string, unknown>) => {
      try {
        const r = await authFetch(`/api/skills/${encodeURIComponent(id)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ changes }),
        })
        if (!r.ok) return { ok: false, error: await problem(r) }
        invalidateQueryCache('tab:skills')
        await load()
        return { ok: true }
      } catch {
        return { ok: false, error: 'Could not reach the server.' }
      }
    },
    [load],
  )

  const deleteSkill = useCallback(
    async (id: string) => {
      try {
        const r = await authFetch(`/api/skills/${encodeURIComponent(id)}`, { method: 'DELETE' })
        if (!r.ok) return { ok: false, error: await problem(r) }
        invalidateQueryCache('tab:skills')
        await load()
        return { ok: true }
      } catch {
        return { ok: false, error: 'Could not reach the server.' }
      }
    },
    [load],
  )

  return {
    // A persisted dev-cache entry can outlive an endpoint shape change. Revalidation runs
    // immediately, but render must not call array methods on that stale object first.
    skills: Array.isArray(query.data) ? (query.data as Skill[]) : [],
    loading: query.initialLoading,
    error: query.error instanceof Error ? query.error.message : null,
    refresh: () => {
      void load()
    },
    createSkill,
    updateSkill,
    deleteSkill,
  }
}
