// The desktop Lucius home tab's right-side artifact viewer — replaces ArtifactCard's default
// fullscreen takeover for exactly this surface (see state/artifactPanel.tsx), matching how
// Claude.ai/ChatGPT canvas keep the chat usable beside an open artifact instead of blocking it
// behind a modal. Delegates the header/body/fetch to ArtifactViewer, the same component
// ArtifactFullscreen uses — this file is just the panel-level chrome (border/bg).

import type { ArtifactRef } from '../../../types/chat'
import { ArtifactViewer } from './ArtifactViewer'
import './artifact-side-panel.css'

export function ArtifactSidePanel({
  artifact,
  onClose,
  onExpand,
}: {
  artifact: ArtifactRef
  onClose: () => void
  onExpand: () => void
}) {
  return (
    <div className="artifact-panel">
      <ArtifactViewer
        artifact={artifact}
        onClose={onClose}
        expandState={{ fullscreen: false, onToggle: onExpand }}
      />
    </div>
  )
}
