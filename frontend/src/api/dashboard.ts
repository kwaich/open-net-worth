import { authFetch } from './client'
import type { DashboardState } from '../components/tabs/overview/types'

// Overview widget-dashboard layout persistence. Mirrors backend/api/dashboard.py:
// GET/PUT /api/dashboard/layout — position/size/config only, never a derived figure.

export async function fetchDashboardLayout(): Promise<DashboardState> {
  const res = await authFetch('/api/dashboard/layout')
  if (!res.ok) throw new Error(`dashboard layout fetch failed (${res.status})`)
  return res.json()
}

export async function saveDashboardLayout(
  state: DashboardState,
  opts?: { keepalive?: boolean },
): Promise<DashboardState> {
  const res = await authFetch('/api/dashboard/layout', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state),
    keepalive: opts?.keepalive,
  })
  if (!res.ok) throw new Error(`dashboard layout save failed (${res.status})`)
  return res.json()
}
