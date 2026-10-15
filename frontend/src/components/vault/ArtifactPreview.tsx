import { desktopArtifactUrl } from '../../platform/desktop'
import { useEffect, useRef, useState } from 'react'
import type { VaultItem } from '../../api/vault'
import { Maximize2 } from '../../ui/icons'
import { sandboxFor } from '../../utils/artifactFrame'
import { isMarkupType, typeLine } from '../../utils/artifactFormats'
import { ArtifactTypeIcon } from '../chat/ArtifactTypeIcon'
import { formatSize } from './vaultFormat'
import { useArtifactContent } from '../../utils/useArtifactContent'

// The width Lucius lays a report out for — a narrower frame shows it scaled down, like a page.
const PAGE_WIDTH = 720

/** A saved artifact drawn in place, in the chat's own sandboxed, network-less frame — scaled
 *  to fit the same frame a PDF's page gets, never interactive; a tap opens it (owner: "for
 *  artifact, we don't show inline preview?"). */
export function ArtifactPreview({ item, onOpen }: { item: VaultItem; onOpen: () => void }) {
  const type = item.artifact_type ?? 'html'
  return isMarkupType(type) ? (
    <MarkupPreview item={item} type={type} onOpen={onOpen} />
  ) : (
    <FilePreview item={item} type={type} onOpen={onOpen} />
  )
}

/** A pdf/xlsx/csv artifact is a file, not a page — a tile naming it, never fetched here. */
function FilePreview({
  item,
  type,
  onOpen,
}: {
  item: VaultItem
  type: NonNullable<VaultItem['artifact_type']>
  onOpen: () => void
}) {
  return (
    <button
      type="button"
      className="vault-artifact vault-artifact--file"
      onClick={onOpen}
      aria-label={`Open ${item.title}`}
    >
      <span className="vault-artifact-file-tile">
        <ArtifactTypeIcon type={type} size={32} />
        <span className="vault-artifact-file-name">{item.filename || item.title}</span>
        <span className="vault-artifact-file-meta">
          {[typeLine({ artifactType: type }), formatSize(item.size_bytes)]
            .filter(Boolean)
            .join(' • ')}
        </span>
      </span>
    </button>
  )
}

function MarkupPreview({
  item,
  type,
  onOpen,
}: {
  item: VaultItem
  type: 'html' | 'svg'
  onOpen: () => void
}) {
  const { frameSrc, failed } = useArtifactContent(item.source_id, type)
  const pageRef = useRef<HTMLSpanElement>(null)
  const [box, setBox] = useState<{ w: number; h: number } | null>(null)
  useEffect(() => {
    const el = pageRef.current
    if (!el) return
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight })
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  const scale = box && box.w > 0 ? Math.min(1, box.w / PAGE_WIDTH) : 1
  return (
    <button
      type="button"
      className="vault-artifact"
      onClick={onOpen}
      aria-label={`Open ${item.title}`}
    >
      <span className="vault-artifact-page" ref={pageRef}>
        {frameSrc && box && box.w > 0 && (
          <iframe
            className="vault-artifact-frame"
            sandbox={sandboxFor(type)}
            src={desktopArtifactUrl(item.source_id, type) ?? frameSrc}
            title={item.title}
            tabIndex={-1}
            aria-hidden="true"
            style={{
              width: box.w / scale,
              height: box.h / scale,
              transform: `scale(${scale})`,
            }}
          />
        )}
        {failed && <span className="vault-pages-error">Can't show it here — open it instead.</span>}
      </span>
      <span className="vault-page-expand" aria-hidden="true">
        <Maximize2 size={13} strokeWidth={1.8} />
        Open
      </span>
    </button>
  )
}
