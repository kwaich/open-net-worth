// ArtifactFileViewer — the "open" experience for a FILE artifact (pdf/xlsx/csv), sibling of the
// iframe-based ArtifactViewer: same header chrome and close/expand controls, but the body is a
// calm file panel (pdf/xlsx — never fetched as text, the raw route answers 415) or a read-only
// table preview (csv). Bytes only ever move through the authenticated download route.

import { useMemo, useState } from 'react'
import type { ArtifactRef } from '../../../types/chat'
import { CSV_PREVIEW_ROWS, extLabel, parseCsv } from '../../../utils/artifactFormats'
import {
  downloadArtifact,
  openArtifactPdf,
  useArtifactContent,
} from '../../../utils/useArtifactContent'
import { ArtifactTypeIcon } from '../../chat/ArtifactTypeIcon'
import { formatSize } from '../../vault/vaultFormat'
import { Button, ErrorState } from '../../ui'
import { Download, ExternalLink, Maximize2, Minimize2, X } from '../../../ui/icons'
import type { ArtifactExpandState } from './ArtifactViewer'
import './artifact-viewer.css'

function useFileActions(artifact: ArtifactRef) {
  const [busy, setBusy] = useState<'download' | 'open' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const run = async (kind: 'download' | 'open', fn: (a: ArtifactRef) => Promise<void>) => {
    setBusy(kind)
    setError(null)
    try {
      await fn(artifact)
    } catch {
      setError(kind === 'open' ? 'Could not open the file.' : 'Could not download the file.')
    } finally {
      setBusy(null)
    }
  }
  return {
    busy,
    error,
    download: () => run('download', downloadArtifact),
    open: () => run('open', openArtifactPdf),
  }
}

function CsvTable({ text }: { text: string }) {
  const { head, body, total } = useMemo(() => {
    const rows = parseCsv(text)
    return {
      head: rows[0] ?? [],
      body: rows.slice(1, CSV_PREVIEW_ROWS + 1),
      total: Math.max(rows.length - 1, 0),
    }
  }, [text])
  if (head.length === 0) return <p className="artifact-file-note">This table is empty.</p>
  return (
    <div className="artifact-csv">
      <div className="artifact-csv-scroll">
        <table className="artifact-csv-table">
          <thead>
            <tr>
              {head.map((c, i) => (
                <th key={i}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((r, ri) => (
              <tr key={ri}>
                {head.map((_, ci) => (
                  <td key={ci}>{r[ci] ?? ''}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > body.length && (
        <p className="artifact-file-note">
          Showing the first {body.length} of {total} rows — download the file for the rest.
        </p>
      )}
    </div>
  )
}

export function ArtifactFilePanel({ artifact }: { artifact: ArtifactRef }) {
  const { busy, error, download, open } = useFileActions(artifact)
  const size = formatSize(artifact.sizeBytes)
  return (
    <div className="artifact-file-panel">
      <span className="artifact-file-icon" aria-hidden="true">
        <ArtifactTypeIcon type={artifact.artifactType} size={32} />
      </span>
      <div className="artifact-file-title">{artifact.title || 'Artifact'}</div>
      <div className="artifact-file-meta">
        {[artifact.filename, size].filter(Boolean).join(' • ')}
      </div>
      <div className="artifact-file-actions">
        <Button
          variant="primary"
          iconLeft={<Download size={14} strokeWidth={1.6} />}
          loading={busy === 'download'}
          onClick={download}
        >
          Download
        </Button>
        {artifact.artifactType === 'pdf' && (
          <Button
            iconLeft={<ExternalLink size={14} strokeWidth={1.6} />}
            loading={busy === 'open'}
            onClick={open}
          >
            Open
          </Button>
        )}
      </div>
      {error && (
        <p className="artifact-file-error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

export function ArtifactFileViewer({
  artifact,
  onClose,
  expandState,
}: {
  artifact: ArtifactRef
  onClose: () => void
  expandState?: ArtifactExpandState
}) {
  const isCsv = artifact.artifactType === 'csv'
  const { raw, failed, retry } = useArtifactContent(artifact.id, artifact.artifactType)
  const { busy, download } = useFileActions(artifact)
  return (
    <div className="artifact-viewer">
      <div className="artifact-viewer-head">
        <div className="artifact-viewer-title">
          <span className="artifact-viewer-title-t">{artifact.title || 'Artifact'}</span>
          <span className="artifact-viewer-title-meta"> • {extLabel(artifact.artifactType)}</span>
        </div>
        <div className="artifact-viewer-actions">
          <button
            type="button"
            className="artifact-viewer-icon-btn"
            onClick={download}
            disabled={busy === 'download'}
            aria-label={`Save as ${extLabel(artifact.artifactType)}`}
            title={`Save as ${extLabel(artifact.artifactType)}`}
          >
            <Download size={15} strokeWidth={1.6} />
          </button>
          {expandState && (
            <button
              type="button"
              className="artifact-viewer-icon-btn"
              onClick={expandState.onToggle}
              aria-label={expandState.fullscreen ? 'Minimize' : 'Expand'}
              title={expandState.fullscreen ? 'Minimize' : 'Expand'}
            >
              {expandState.fullscreen ? (
                <Minimize2 size={15} strokeWidth={1.6} />
              ) : (
                <Maximize2 size={15} strokeWidth={1.6} />
              )}
            </button>
          )}
          <button
            type="button"
            className="artifact-viewer-icon-btn"
            onClick={onClose}
            aria-label="Close"
            title="Close"
          >
            <X size={18} strokeWidth={1.6} />
          </button>
        </div>
      </div>
      <div className="artifact-viewer-body">
        {!isCsv ? (
          <ArtifactFilePanel artifact={artifact} />
        ) : failed ? (
          <ErrorState
            title="Could not load artifact"
            message="The file may have been removed."
            onRetry={retry}
          />
        ) : raw === null ? (
          <div className="artifact-card__skeleton" aria-hidden="true">
            <div className="artifact-card__skeleton-bar" />
            <div className="artifact-card__skeleton-bar" />
          </div>
        ) : (
          <CsvTable text={raw} />
        )}
      </div>
    </div>
  )
}
