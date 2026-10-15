// Overview v2 — a small seam letting `WidgetShell` (owned by this lane) offer
// two grid-level affordances to every widget content component WITHOUT any
// content-lane file (`widgets/*.tsx`) needing to change: the widget's
// `def.icon` as its default title-lead glyph (item 10 of the parity audit),
// and inline double-click-to-rename on the title (item 6) — both wired from
// `DashboardGrid.tsx`, which is the one place that already knows the
// instanceId/def for whatever it's rendering. A widget component that DOES
// pass its own explicit `lead` (e.g. the daily briefing's Lucius spark) still
// wins — the context only supplies a DEFAULT.

import { createContext, useContext, useEffect } from 'react'
import type { ReactNode } from 'react'
import type { KebabItem } from '../planning/KebabMenu'

export interface GridItemContextValue {
  instanceId: string
  editing: boolean
  /** The resolved def's icon — WidgetShell's default `lead` when the widget
   * itself passes none. */
  icon?: ReactNode
  /** Commit a rename (or `undefined` to clear back to the default title). */
  onRenameTitle: (title: string | undefined) => void
  collapsed?: boolean
  onToggleCollapse?: () => void
  onOpenFullscreen?: () => void
  /** Listed first in the header ⋯, ahead of Full screen / Collapse (the widget's editor). */
  primaryMenuItems?: KebabItem[]
  menuItems?: KebabItem[]
  /** Stable across renders — see `useFitContentWhen`. */
  setFit?: (instanceId: string, fit: boolean) => void
}

export const GridItemContext = createContext<GridItemContextValue | null>(null)

export function useGridItemContext(): GridItemContextValue | null {
  return useContext(GridItemContext)
}

/**
 * A fixed-size widget whose box is sized for content it doesn't have yet (the
 * net-worth card before there's a chart) fits its content instead, until it does.
 */
export function useFitContentWhen(fit: boolean): void {
  const ctx = useContext(GridItemContext)
  const instanceId = ctx?.instanceId
  const setFit = ctx?.setFit
  useEffect(() => {
    if (!instanceId || !setFit) return
    setFit(instanceId, fit)
    return () => setFit(instanceId, false)
  }, [instanceId, setFit, fit])
}
