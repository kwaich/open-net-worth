import { desktopArtifactUrl } from '../../../platform/desktop'
// ArtifactViewer — the full "open" experience for an artifact: header (preview/source toggle,
// title, copy/download, expand, close) + body (sandboxed rendered iframe, or raw source).
// Shared by ArtifactSidePanel (home tab's right-side panel) and ArtifactCard's own fullscreen
// Modal (dock/mobile) — one implementation, so the two never drift on what "open" looks like.

import { useMemo, useState } from 'react'
import Prism from 'prismjs'
import 'prismjs/components/prism-markup'
import 'prismjs/components/prism-clike'
import 'prismjs/components/prism-css'
import 'prismjs/components/prism-javascript'
import type { ArtifactRef } from '../../../types/chat'
import { sandboxFor } from '../../../utils/artifactFrame'
import { extLabel, isMarkupArtifact, type MarkupArtifact } from '../../../utils/artifactFormats'
import { downloadArtifactContent, useArtifactContent } from '../../../utils/useArtifactContent'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { ErrorState } from '../../ui'
import {
  Check,
  ChevronDown,
  Code2,
  Copy,
  Download,
  Eye,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  X,
} from '../../../ui/icons'
import { ArtifactFileViewer } from './ArtifactFileViewer'
import './artifact-viewer.css'

type View = 'preview' | 'source'

/** The expand/minimize control's state — omit entirely on a surface with no smaller state to
 * return to (the dock/mobile fullscreen path): there's nowhere for it to go, so no button. */
export interface ArtifactExpandState {
  fullscreen: boolean
  onToggle: () => void
}

/** html/svg open in the sandboxed iframe; pdf/xlsx/csv are files and get ArtifactFileViewer. */
export function ArtifactViewer(props: {
  artifact: ArtifactRef
  onClose: () => void
  expandState?: ArtifactExpandState
}) {
  const { artifact } = props
  return isMarkupArtifact(artifact) ? (
    <MarkupArtifactViewer {...props} artifact={artifact} />
  ) : (
    <ArtifactFileViewer {...props} />
  )
}

/** Covered until its page has loaded. The app is `color-scheme: dark` and an artifact page
 *  declares none, so until the page's own styles paint, the frame shows a white canvas — a
 *  flash as the phone's sheet slides up (owner: "there is a white flash"). Mounted fresh on
 *  each Preview, so switching back from Code covers it again until it reloads.
 *
 *  A COVER over the frame, never the frame itself hidden: iOS WebKit does not set up touch
 *  scrolling for an iframe whose document loads while it is `visibility: hidden`, and only
 *  recovers on a re-layout — the report could not be scrolled until the app was backgrounded
 *  and reopened (owner, 2026-10-06). The frame stays visible and laid out from its first paint. */
function ArtifactFrame({
  artifact,
  frameSrc,
}: {
  artifact: MarkupArtifact
  frameSrc: string | null
}) {
  const [loaded, setLoaded] = useState(false)
  return (
    <>
      <iframe
        className="artifact-viewer-frame"
        sandbox={sandboxFor(artifact.artifactType)}
        src={desktopArtifactUrl(artifact.id, artifact.artifactType) ?? frameSrc ?? undefined}
        title={artifact.title || 'Artifact'}
        onLoad={() => setLoaded(true)}
      />
      {!loaded && <div className="artifact-viewer-frame-cover" aria-hidden="true" />}
    </>
  )
}

function MarkupArtifactViewer({
  artifact,
  onClose,
  expandState,
}: {
  artifact: MarkupArtifact
  onClose: () => void
  expandState?: ArtifactExpandState
}) {
  const { raw, frameSrc, failed, retry } = useArtifactContent(artifact.id, artifact.artifactType)
  const [view, setView] = useState<View>('preview')
  const [copyMenuOpen, setCopyMenuOpen] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const ready = frameSrc !== null
  const isMobile = useIsMobile()

  // Highlighted PER LINE, not as one Prism.highlight() call over the whole document — a
  // single call's output can't be safely split by '\n' afterward to build line-numbered rows
  // (a token span wrapping a multi-line construct, e.g. a CSS comment, would get cut in half,
  // corrupting the HTML), and per-line rows are what let each line wrap independently while
  // keeping its OWN number anchored beside it (owner report, real device: long lines needed
  // to wrap instead of forcing horizontal scroll). The cost — a multi-line comment/string
  // loses cross-line token context, since each line is tokenized on its own — is the standard,
  // accepted trade-off most practical code viewers make for exactly this reason.
  //
  // Highlighting every line as plain 'markup' ALSO loses <style>/<script> embedding — Prism's
  // markup grammar only switches to the CSS/JS sub-grammar for those tags when it can see the
  // whole document at once (owner report, real device: a report's entire <style> block stayed
  // one flat color, no property/value distinction at all). A tiny state machine tracks which
  // section each line is actually IN, so each line highlights with the grammar that section
  // needs — imperfect for a rule that itself spans multiple lines (same accepted trade-off as
  // above), but correct for the common case of one declaration per line.
  //
  // Prism's own token class names (.property, .selector, .string, .keyword, .tag, ...) are
  // plain, un-namespaced English words — exactly the shape likely to collide with SOME
  // unrelated existing class somewhere in this app's ~12k-line stylesheet (owner report, real
  // device: unexplained white background bands behind specific lines — a pre-existing global
  // rule for an unrelated ".property"-or-similar class, not anything in this file, bleeding a
  // background-color onto the code view since nothing here overrides it). Every token class
  // Prism emits is prefixed here so none of them can ever collide with anything else again.
  const highlightedLines = useMemo(() => {
    if (raw === null) return null
    let section: 'markup' | 'css' | 'js' = 'markup'
    return raw.split('\n').map((line) => {
      const grammar =
        (section === 'css'
          ? Prism.languages.css
          : section === 'js'
            ? Prism.languages.javascript
            : Prism.languages.markup) ?? {}
      const html = Prism.highlight(line, grammar, section)
      // The line CONTAINING the tag stays highlighted as markup (mixed content — the tag
      // itself is HTML); only lines AFTER it switch grammar.
      if (/<style[\s>]/.test(line)) section = 'css'
      else if (/<\/style>/.test(line)) section = 'markup'
      else if (/<script[\s>]/.test(line)) section = 'js'
      else if (/<\/script>/.test(line)) section = 'markup'
      return html.replace(
        /class="([^"]+)"/g,
        (_m, cls: string) =>
          `class="${cls
            .split(' ')
            .map((c) => `pr-${c}`)
            .join(' ')}"`,
      )
    })
  }, [raw])

  const copy = async () => {
    if (raw === null) return
    await navigator.clipboard.writeText(raw)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
    setMobileMenuOpen(false)
  }

  const download = () => {
    if (raw === null) return
    downloadArtifactContent(raw, artifact)
    setCopyMenuOpen(false)
    setMobileMenuOpen(false)
  }

  return (
    <div className="artifact-viewer">
      {isMobile ? (
        // Bottom-sheet header (owner, 2026-08-13: "on mobile it should show like a bottom
        // sheet") — X/title/kebab instead of the desktop's icon-button row, which reads as a
        // cramped toolbar at phone width. The kebab folds every desktop action (preview/source
        // toggle, copy, download) into one menu instead of competing for header space.
        <div className="artifact-viewer-head artifact-viewer-head--mobile">
          <button
            type="button"
            className="artifact-viewer-icon-btn"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={18} strokeWidth={1.6} />
          </button>
          <span className="artifact-viewer-title artifact-viewer-title--mobile">
            {artifact.title || 'Artifact'}
          </span>
          <div className="artifact-viewer-mobile-menu-wrap">
            <button
              type="button"
              className="artifact-viewer-icon-btn"
              onClick={() => setMobileMenuOpen((o) => !o)}
              aria-label="More options"
              aria-expanded={mobileMenuOpen}
            >
              <MoreHorizontal size={18} />
            </button>
            {mobileMenuOpen && (
              <>
                <div
                  className="artifact-viewer-copy-menu-scrim"
                  onClick={() => setMobileMenuOpen(false)}
                />
                <div className="artifact-viewer-copy-menu artifact-viewer-mobile-menu" role="menu">
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={view === 'preview'}
                    onClick={() => {
                      setView('preview')
                      setMobileMenuOpen(false)
                    }}
                  >
                    <Eye size={14} strokeWidth={1.6} />
                    Preview
                    {view === 'preview' && (
                      <Check size={14} className="artifact-viewer-menu-check" />
                    )}
                  </button>
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={view === 'source'}
                    onClick={() => {
                      setView('source')
                      setMobileMenuOpen(false)
                    }}
                  >
                    <Code2 size={14} strokeWidth={1.6} />
                    Code
                    {view === 'source' && (
                      <Check size={14} className="artifact-viewer-menu-check" />
                    )}
                  </button>
                  <div className="artifact-viewer-menu-divider" />
                  <button type="button" role="menuitem" onClick={copy} disabled={!ready}>
                    <Copy size={14} strokeWidth={1.4} />
                    Copy
                  </button>
                  <button type="button" role="menuitem" onClick={download} disabled={!ready}>
                    <Download size={14} strokeWidth={1.4} />
                    Save as {extLabel(artifact.artifactType)}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      ) : (
        <div className="artifact-viewer-head">
          <div className="artifact-viewer-view-toggle" role="tablist" aria-label="Artifact view">
            <button
              type="button"
              className={view === 'preview' ? 'on' : ''}
              role="tab"
              aria-selected={view === 'preview'}
              title="Preview"
              aria-label="Preview"
              onClick={() => setView('preview')}
            >
              <Eye size={15} strokeWidth={1.6} />
            </button>
            <button
              type="button"
              className={view === 'source' ? 'on' : ''}
              role="tab"
              aria-selected={view === 'source'}
              title="View source"
              aria-label="View source"
              onClick={() => setView('source')}
            >
              <Code2 size={15} strokeWidth={1.6} />
            </button>
          </div>

          <div className="artifact-viewer-title">
            <span className="artifact-viewer-title-t">{artifact.title || 'Artifact'}</span>
            <span className="artifact-viewer-title-meta"> • {extLabel(artifact.artifactType)}</span>
          </div>

          <div className="artifact-viewer-actions">
            {/* .artifact-viewer-copy (the pill) needs overflow:hidden for its own rounded-corner
                clipping (see artifact-viewer.css) — that same overflow:hidden would ALSO clip
                the dropdown popping out below it if the menu were a child of that element (owner
                report, real device: tapping the caret "did nothing" — it was actually opening,
                just invisible, clipped by its own ancestor). The menu is a sibling of the pill
                inside THIS wrapper instead, which owns the position:relative anchor but has no
                overflow of its own to clip anything. */}
            <div className="artifact-viewer-copy-wrap">
              <div className="artifact-viewer-copy">
                <button
                  type="button"
                  className="artifact-viewer-copy-btn"
                  onClick={copy}
                  disabled={!ready}
                >
                  <Copy size={13} strokeWidth={1.4} />
                  {copied ? 'Copied' : 'Copy'}
                </button>
                <button
                  type="button"
                  className="artifact-viewer-copy-caret"
                  onClick={() => setCopyMenuOpen((o) => !o)}
                  aria-label="More options"
                  aria-expanded={copyMenuOpen}
                  disabled={!ready}
                >
                  <ChevronDown size={13} strokeWidth={1.6} />
                </button>
              </div>
              {copyMenuOpen && (
                <>
                  <div
                    className="artifact-viewer-copy-menu-scrim"
                    onClick={() => setCopyMenuOpen(false)}
                  />
                  <div className="artifact-viewer-copy-menu" role="menu">
                    <button type="button" role="menuitem" onClick={download}>
                      Download as {extLabel(artifact.artifactType)}
                    </button>
                  </div>
                </>
              )}
            </div>
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
      )}

      <div className="artifact-viewer-body">
        {failed ? (
          <ErrorState
            title="Could not load artifact"
            message="The file may have been removed."
            onRetry={retry}
          />
        ) : !ready ? (
          <div className="artifact-card__skeleton" aria-hidden="true">
            <div className="artifact-card__skeleton-bar" />
            <div className="artifact-card__skeleton-bar" />
          </div>
        ) : view === 'source' ? (
          <div className="artifact-viewer-source">
            {highlightedLines?.map((lineHtml, i) => (
              <div key={i} className="artifact-viewer-source-line">
                <span className="artifact-viewer-source-lineno" aria-hidden="true">
                  {i + 1}
                </span>
                <span
                  className="artifact-viewer-source-linecode"
                  // Prism.highlight escapes each line's source before wrapping tokens — this
                  // is colored, escaped markup of the artifact's OWN text, not raw model
                  // output.
                  dangerouslySetInnerHTML={{ __html: lineHtml }}
                />
              </div>
            ))}
          </div>
        ) : (
          <ArtifactFrame artifact={artifact} frameSrc={frameSrc} />
        )}
      </div>
    </div>
  )
}
