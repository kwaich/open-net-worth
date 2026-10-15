import { authFetch } from './client'

export type BuilderEngineId = 'codex' | 'claude'
export type BuilderMode = 'plan' | 'build'
export type BuilderRunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type BuilderEventKind = 'status' | 'assistant' | 'tool' | 'output' | 'error'

export interface BuilderEngineStatus {
  id: BuilderEngineId
  label: string
  installed: boolean
  version: string
  command: string
  build_is_sandboxed: boolean
  guidance: string
}

export interface BuilderStatus {
  workspace: string
  workspace_name: string
  workspace_valid: boolean
  error: string
  engines: BuilderEngineStatus[]
  active_run_id: string | null
}

export interface BuilderEvent {
  seq: number
  kind: BuilderEventKind
  text: string
  created_at: string
}

export interface BuilderRun {
  id: string
  engine: BuilderEngineId
  mode: BuilderMode
  prompt: string
  status: BuilderRunStatus
  events: BuilderEvent[]
  started_at: string | null
  finished_at: string | null
  error: string
  workspace_changes: string[]
}

async function readJson<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail =
      typeof data?.detail === 'string' ? data.detail : `Request failed (${response.status}).`
    throw new Error(detail)
  }
  return data as T
}

export async function getBuilderStatus(): Promise<BuilderStatus> {
  return readJson<BuilderStatus>(await authFetch('/api/builder/status'))
}

export async function startBuilderRun(input: {
  engine: BuilderEngineId
  mode: BuilderMode
  prompt: string
}): Promise<BuilderRun> {
  return readJson<BuilderRun>(
    await authFetch('/api/builder/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }),
  )
}

export async function getBuilderRun(
  id: string,
  after = 0,
  signal?: AbortSignal,
): Promise<BuilderRun> {
  return readJson<BuilderRun>(
    await authFetch(`/api/builder/runs/${encodeURIComponent(id)}?after=${after}`, { signal }),
  )
}

export async function stopBuilderRun(id: string): Promise<BuilderRun> {
  return readJson<BuilderRun>(
    await authFetch(`/api/builder/runs/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  )
}
