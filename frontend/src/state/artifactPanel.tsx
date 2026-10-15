// ArtifactPanel context — lets ArtifactCard open an artifact in a side panel instead of its
// default fullscreen Modal, WITHOUT threading a prop through ChatPanel/MessageBubble (both
// shared by the docked dock, mobile, and the desktop home tab, none of which know or care
// about this). Only LuciusHome provides it; everywhere else useArtifactPanel() returns null
// and ArtifactCard falls back to its own Modal — the same component works unmodified in every
// surface.

import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'
import type { ArtifactRef } from '../types/chat'

type ArtifactPanelValue = (artifact: ArtifactRef) => void

const ArtifactPanelContext = createContext<ArtifactPanelValue | null>(null)

export function ArtifactPanelProvider({
  onOpen,
  children,
}: {
  onOpen: ArtifactPanelValue
  children: ReactNode
}) {
  return <ArtifactPanelContext.Provider value={onOpen}>{children}</ArtifactPanelContext.Provider>
}

/** null outside a provider — callers fall back to their own default (a Modal). */
export function useArtifactPanel(): ArtifactPanelValue | null {
  return useContext(ArtifactPanelContext)
}
