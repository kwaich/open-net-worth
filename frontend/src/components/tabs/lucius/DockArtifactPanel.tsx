// DockArtifactPanel — the artifact side panel for the DOCKED floating chat (used from every
// tab other than the Lucius home tab itself, e.g. Overview). Owner, 2026-08-13: "when user use
// lucius side panel! the artifact must open as the modal on the side, not fullscreen" — this
// mirrors LuciusHome's own ArtifactSidePanel (same ArtifactViewer, same desktop header), just
// positioned adjacent to the dock instead of living inside its grid layout, since the dock is
// a `position: fixed` overlay with no grid of its own to be a column of.

import type { ArtifactRef } from '../../../types/chat'
import { ArtifactViewer } from './ArtifactViewer'
import './dock-artifact-panel.css'

export function DockArtifactPanel({
  artifact,
  onClose,
}: {
  artifact: ArtifactRef
  onClose: () => void
}) {
  return (
    <div className="dock-artifact-panel" data-lucius-portal>
      <ArtifactViewer artifact={artifact} onClose={onClose} />
    </div>
  )
}
