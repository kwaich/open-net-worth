import { useState } from 'react'
import type { ArtifactRef } from '../../types/chat'
import { useArtifactPanel } from '../../state/artifactPanel'
import { ArtifactFullscreen } from '../tabs/lucius/ArtifactFullscreen'
import { downloadArtifact } from '../../utils/useArtifactContent'
import { typeLine } from '../../utils/artifactFormats'
import { Download } from '../../ui/icons'
import { ArtifactTypeIcon } from './ArtifactTypeIcon'
import { SaveToVaultButton } from '../vault/SaveToVaultButton'

// Compact inline chip for a Lucius-generated artifact (chart/report/visual document) — NOT a
// live-rendered preview (owner, 2026-08-13: matches the Claude.ai reference — icon, title,
// type, one action). The document itself only ever renders once actually opened, inside a
// SANDBOXED iframe (ArtifactViewer/artifactFrame.ts): sandbox="allow-scripts" only, never
// allow-same-origin — a null origin, so no cookies, no parent DOM, no credentialed API calls,
// with an egress-blocking CSP. This card fetches nothing itself for the "open" path;
// ArtifactViewer does its own independent fetch once opened, same as the side panel — the
// "Download" button below is the one exception, fetching just for that one-shot save.
//
// Two independent targets share the row, as SIBLING <button>s — not one nested inside the
// other, which is a real ARIA anti-pattern (a widget with role=button must not contain
// focusable descendants) as well as invalid HTML if the outer were a real <button>. Mirrors
// this codebase's own established shape for exactly this "row + trailing action" case
// (ConversationRow: a main button + a separate kebab button, chat/ConversationMenu.tsx).
// .artifact-chip-main covers the icon+title+meta and opens the viewer — the side panel when
// one is provided (LuciusHome — see state/artifactPanel.tsx), a true edge-to-edge fullscreen
// takeover otherwise (the docked panel and mobile, neither of which has room for a split
// view). .artifact-chip-open is a plain "Download" (owner, 2026-08-13: the original
// "Download and open" label implied opening the just-downloaded FILE in the OS afterward —
// not achievable from a web page; a browser has no API to open a local file it just saved,
// by design. Opening the in-app viewer instead would've been a different, unasked-for
// action, so this is just the download, nothing else). "Save" beside it keeps the artifact in
// the user's Vault (components/vault/SaveToVaultButton.tsx) — saving is the user's call.

export function ArtifactCard({ artifact }: { artifact: ArtifactRef }) {
  const openPanel = useArtifactPanel()
  const [fullscreen, setFullscreen] = useState(false)
  const open = () => (openPanel ? openPanel(artifact) : setFullscreen(true))

  return (
    <div className="artifact-chip-wrap">
      <div className="artifact-chip">
        <button type="button" className="artifact-chip-main" onClick={open}>
          <span className="artifact-chip-icon" aria-hidden="true">
            <ArtifactTypeIcon type={artifact.artifactType} />
          </span>
          <span className="artifact-chip-text">
            <span className="artifact-chip-title">{artifact.title || 'Artifact'}</span>
            <span className="artifact-chip-meta">{typeLine(artifact)}</span>
          </span>
        </button>
        <SaveToVaultButton artifactId={artifact.id} />
        <button
          type="button"
          className="artifact-chip-open"
          onClick={() => void downloadArtifact(artifact)}
          aria-label="Download"
          title="Download"
        >
          <Download size={13} strokeWidth={1.6} />
          <span className="artifact-chip-label">Download</span>
        </button>
      </div>

      {/* Skipped entirely when a panel provider is present (LuciusHome) — "Open" above never
          sets `fullscreen` in that case. */}
      {!openPanel && fullscreen && (
        <ArtifactFullscreen artifact={artifact} onClose={() => setFullscreen(false)} />
      )}
    </div>
  )
}
