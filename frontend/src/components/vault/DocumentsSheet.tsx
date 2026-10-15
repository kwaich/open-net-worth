import { useState } from 'react'
import { useIsMobile } from '../../hooks/useIsMobile'
import type { ArtifactRef } from '../../types/chat'
import { ArtifactFullscreen } from '../tabs/lucius/ArtifactFullscreen'
import { Modal } from '../ui/Modal'
import { VaultPanel } from './VaultPanel'

/** Documents over the page, from the chat side panel on any tab (owner: "is there a way to access
 *  documents from side panel? apart from mention?"). The same panel the Lucius tab shows: Ask
 *  Lucius puts the item in the side panel's message and closes this; a saved artifact opens full
 *  screen, and closing it lands back here. */
export function DocumentsSheet({ onClose }: { onClose: () => void }) {
  const [artifact, setArtifact] = useState<ArtifactRef | null>(null)
  // On a computer the side panel stays in view beside this (a modal leaves the chat undimmed),
  // so a row drags straight into it (owner: "i can't drag the document cell into the side panel").
  const isMobile = useIsMobile()
  return (
    <>
      <Modal open={!artifact} onClose={onClose} title="Documents" size="lg" className="vault-sheet">
        <VaultPanel
          layout="sheet"
          draggableRows={!isMobile}
          onClose={onClose}
          onOpenArtifact={setArtifact}
          onAsked={onClose}
        />
      </Modal>
      {artifact && <ArtifactFullscreen artifact={artifact} onClose={() => setArtifact(null)} />}
    </>
  )
}
