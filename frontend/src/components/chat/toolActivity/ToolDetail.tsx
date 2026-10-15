// The expanded view of one tool call: what was asked, and what came back.
//
// Two labelled boxes, JSON syntax-highlighted through Prism the same way
// `ArtifactViewer.tsx` does it — including the `pr-` class prefix, which exists because
// Prism's own un-namespaced token names (.string, .property, .keyword) collided with
// unrelated rules elsewhere in this app's stylesheet. Same reason applies here, so the same
// prefix is used; the colour rules live beside the other chat styles in `chat.css`.

import Prism from 'prismjs'
import 'prismjs/components/prism-json'
import { useMemo, useState } from 'react'
import { Check, Copy } from '../../../ui/icons'
import { stripExternalMcpFence } from '../../../utils/untrusted'

/** Prism's token classes, namespaced — see the header note. Returns null when the grammar
 *  did not load, so the caller renders plain text rather than nothing. */
function highlightJson(text: string): string | null {
  const grammar = Prism.languages.json
  if (!grammar) return null
  const html = Prism.highlight(text, grammar, 'json')
  return html.replace(
    /class="([^"]+)"/g,
    (_m, cls: string) =>
      `class="${cls
        .split(' ')
        .map((c) => `pr-${c}`)
        .join(' ')}"`,
  )
}

/** Pretty-print when it IS JSON, leave it exactly alone when it is not. */
function format(value: unknown): { text: string; json: boolean } {
  if (typeof value === 'string') {
    try {
      return { text: JSON.stringify(JSON.parse(value), null, 2), json: true }
    } catch {
      return { text: value, json: false }
    }
  }
  try {
    return { text: JSON.stringify(value, null, 2), json: true }
  } catch {
    return { text: String(value), json: false }
  }
}

/** "2.1 KB" / "840 B" — honest size of what came back, not a guess at its importance. */
function sizeLabel(text: string): string {
  const bytes = new TextEncoder().encode(text).length
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="msg-toolact__copy"
      aria-label="Copy"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true)
          setTimeout(() => setCopied(false), 1200)
        })
      }}
    >
      {copied ? <Check size={11} strokeWidth={1.6} /> : <Copy size={11} strokeWidth={1.6} />}
    </button>
  )
}

function Box({ title, value, tone }: { title: string; value: unknown; tone?: 'error' }) {
  const { text, json } = useMemo(() => format(value), [value])
  const html = useMemo(() => (json ? highlightJson(text) : null), [json, text])
  return (
    <div className="msg-toolact__box">
      <div className="msg-toolact__box-head">
        <span className="msg-toolact__box-title">{title}</span>
        <span className="msg-toolact__box-meta">{sizeLabel(text)}</span>
        <CopyButton text={text} />
      </div>
      {html !== null ? (
        <pre
          className={`msg-toolact__code${tone === 'error' ? ' msg-toolact__code--error' : ''}`}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className={`msg-toolact__code${tone === 'error' ? ' msg-toolact__code--error' : ''}`}>
          {text}
        </pre>
      )}
    </div>
  )
}

export function ToolDetail({
  input,
  result,
  loading,
  loadingLabel,
  failedToLoad,
  onRetry,
  resultFailed,
}: {
  input?: Record<string, unknown>
  result?: string
  loading: boolean
  loadingLabel: string
  failedToLoad: boolean
  /** Absent when a retry is impossible (no session to fetch against) — the button is then
   *  hidden rather than offered as a control that cannot do anything. */
  onRetry?: () => void
  /** The call itself failed (not the fetch) — the Result box says so in its own right, so
   *  an expanded failure does not look identical to an expanded success. */
  resultFailed?: boolean
}) {
  if (loading) {
    return (
      <div className="msg-toolact__detail msg-toolact__detail--running">
        <span className="msg-search-loading">
          <span />
          <span />
          <span />
        </span>
        {loadingLabel}
      </div>
    )
  }
  if (failedToLoad) {
    return (
      <div className="msg-toolact__detail">
        <div className="msg-toolact__note">Couldn’t load the full result.</div>
        {onRetry && (
          <button type="button" className="msg-toolact__retry" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    )
  }
  if (input === undefined && result === undefined) {
    return (
      <div className="msg-toolact__detail">
        <div className="msg-toolact__note">No result data.</div>
      </div>
    )
  }
  return (
    <div className="msg-toolact__detail">
      {input !== undefined && <Box title="Request" value={input} />}
      {/* The UNVERIFIED external-MCP fence is stripped for DISPLAY only (2026-08-22) — what
          the model received is byte-for-byte unchanged. */}
      {result !== undefined && (
        <Box
          title={resultFailed ? 'Error' : 'Result'}
          value={stripExternalMcpFence(result)}
          tone={resultFailed ? 'error' : undefined}
        />
      )}
    </div>
  )
}
