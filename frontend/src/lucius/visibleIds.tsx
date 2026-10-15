// visibleIds.tsx — shared visible-entity-ids context.
//
// Runs the viewport scan (useVisibleIds) ONCE at the RouterShell level and
// distributes the result via context so LuciusComposer, Autocomplete, and
// ScreenContextBar all read the same Set without each triggering their own
// scroll/resize listeners.

import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'
import { useVisibleIds } from './useScreenContract'
import type { UseVisibleIdsResult } from './useScreenContract'

const VisibleIdsContext = createContext<UseVisibleIdsResult | null>(null)

/** Mount once inside RouterShell (inside ScreenSnapshotProvider). */
export function VisibleIdsProvider({ children }: { children: ReactNode }) {
  const value = useVisibleIds()
  return <VisibleIdsContext.Provider value={value}>{children}</VisibleIdsContext.Provider>
}

/** Read the shared visibleIds Set + rescan(). Must be inside <VisibleIdsProvider>. */
export function useVisibleIdsValue(): UseVisibleIdsResult {
  const ctx = useContext(VisibleIdsContext)
  if (!ctx) throw new Error('useVisibleIdsValue must be used within <VisibleIdsProvider>')
  return ctx
}
