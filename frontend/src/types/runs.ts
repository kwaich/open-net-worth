// Run history types — shared between agent runs and task runs.
// Matches the backend RunHistoryStore record schema (backend/db/run_history_store.py).

export interface AgentRun {
  id: string
  /** Always 'agent' for records returned from GET /api/agents/{id}/runs. */
  kind: 'agent' | 'task'
  entity_id: string
  entity_name: string
  started_at: string // ISO-8601
  finished_at: string | null // ISO-8601, null if still running
  duration_ms: number | null // milliseconds, null if still running
  status: 'ok' | 'error' | 'skipped' | 'running'
  /** Full unredacted error text (local, auth-gated). Null on success. */
  error: string | null
  /** One-line result summary e.g. "2 findings". */
  summary: string | null
  /** "manual" | "dispatch" | "schedule" | "schedule:<task_id>" */
  trigger: string
  /** Human-readable label for the schedule that triggered this run (e.g. "Weekly review").
   * Null for non-schedule triggers or when the task no longer exists. Enriched at read time. */
  trigger_title: string | null
  finding_id: string | null
  cost_usd: number | null
  /** Structured tool-call log captured during the run. Each entry is either a
   * "calling" event (when the tool was invoked) or a result event (ok/error). */
  steps: Array<{
    t: string // ISO timestamp
    type?: string // "log" for captured log lines; absent for tool-call steps
    // tool-call step fields (type != "log")
    tool?: string // tool name
    input?: unknown // raw tool input (calling events)
    result?: string // raw result text (result events, truncated at 2000 chars)
    ok?: boolean // true = success, false = error result (result events)
    status?: string // "calling" (calling events)
    // log entry fields (type == "log")
    level?: string // "WARNING" | "ERROR" | "CRITICAL"
    logger?: string // python logger name e.g. "backend.ai.assistant"
    msg?: string // log message
  }>
}

/** TaskRun has the same shape as AgentRun but kind is always 'task'.
 * Returned from GET /api/scheduled-tasks/{id}/runs. */
export type TaskRun = AgentRun
