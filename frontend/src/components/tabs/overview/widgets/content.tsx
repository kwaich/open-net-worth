// Overview v2 — Content widget group: text, image, embed, and the two
// Lucius-generated widget type (`lux-html`).
//
//   - text: a Markdown note. `instance.config.markdown` is the source; edit
//     and preview share ONE textual value (no separate draft state to lose),
//     rendered via the app's existing react-markdown + remark-gfm stack
//     (MessageBubble/Inbox/SkillDetailPane already depend on it — no new
//     Markdown library).
//   - image: `instance.config.url` — a URL field + <img> preview. No upload
//     pipeline (out of scope per the handoff's own "a picture" framing).
//   - embed: `instance.config.url` (seeded from the def's `settings` schema)
//     — an <iframe src={url}>. User-supplied EXTERNAL content, so this is a
//     different trust boundary than the artifact sandbox below; see the
//     sandbox-attribute comment on EmbedWidget for the reasoning.
//   - lux-html ("Lucius custom"): pins a REAL artifact (created via the
//     existing `create_artifact` tool in a normal chat turn) into the grid,
//     rendered through the EXISTING `ArtifactCard`/`utils/artifactFrame.ts`
//     sandboxed-iframe pipeline VERBATIM — zero new sandboxing code. The
//     picker is backed by the new additive `GET /api/artifacts` (recent
//     metadata only, never bytes). "Create with Lucius" opens the real panel
//     prefilled asking Lucius to build a dashboard artifact (via onAskLucius)
//     — never a second mocked generation pipeline. See ADR 2026-07-22-
//     overview-tab-redesign-faithful-port.md, decision 5.

import { useCallback, useEffect, useState } from 'react'
import type { ComponentType } from 'react'
import ReactMarkdown from 'react-markdown'
import { SAFE_MARKDOWN_COMPONENTS } from '../../../ui/safeMarkdown'
import remarkGfm from 'remark-gfm'
import { fetchRecentArtifacts, type RecentArtifact } from '../../../../api/artifacts'
import type { ArtifactRef } from '../../../../types/chat'
import { Button, Select, SegmentedControl, Text, TextArea, TextField } from '../../../ui'
import { ArtifactCard } from '../../../chat/ArtifactCard'
import type { WidgetContentProps } from '../types'
import { WidgetShell } from '../WidgetShell'
import { WidgetPlaceholder } from '../WidgetPlaceholder'
import { Spark } from '../../../../lucius/identity'

// ── Text (Markdown note) ─────────────────────────────────────────────────────

const TextNote: ComponentType<WidgetContentProps> = ({ instance, def, onPatch }) => {
  const markdown = (instance.config?.markdown as string) || ''
  const [mode, setMode] = useState<'edit' | 'preview'>(markdown ? 'preview' : 'edit')

  const toggle = (
    <SegmentedControl
      ariaLabel="Edit or preview"
      value={mode}
      onChange={setMode}
      options={[
        { value: 'edit', label: 'Edit' },
        { value: 'preview', label: 'Preview' },
      ]}
    />
  )

  return (
    <WidgetShell title={instance.title} base={def.title} actions={toggle}>
      {mode === 'edit' ? (
        <TextArea
          className="ov-note-textarea"
          rows={6}
          value={markdown}
          onChange={(e) => onPatch({ config: { ...instance.config, markdown: e.target.value } })}
          placeholder="Write in Markdown — # heading, **bold**, *italic*, - list, [link](url)…"
        />
      ) : markdown ? (
        <div className="ov-note-md" onClick={() => setMode('edit')} title="Click to edit">
          <ReactMarkdown
            remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
            components={SAFE_MARKDOWN_COMPONENTS}
          >
            {markdown}
          </ReactMarkdown>
        </div>
      ) : (
        <div className="ov-note-md ov-note-empty" onClick={() => setMode('edit')}>
          <WidgetPlaceholder detail="Click Edit to write a note." />
        </div>
      )}
    </WidgetShell>
  )
}

// ── Image ─────────────────────────────────────────────────────────────────────

function normalizeUrl(raw: string): string {
  const trimmed = raw.trim()
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`
}

const ImageWidget: ComponentType<WidgetContentProps> = ({ instance, def, onPatch }) => {
  const url = (instance.config?.url as string) || ''
  const [draft, setDraft] = useState(url)
  const [broken, setBroken] = useState(false)

  useEffect(() => {
    setDraft(url)
    setBroken(false)
  }, [url])

  const save = () => {
    if (!draft.trim()) return
    onPatch({ config: { ...instance.config, url: normalizeUrl(draft) } })
  }
  const clear = () => onPatch({ config: { ...instance.config, url: '' } })

  if (!url) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <div className="ov-image-setup">
          <Text as="p" size="xs" tone="faint">
            A picture — your goal, a chart screenshot, a family photo.
          </Text>
          <div className="ov-image-setup-row">
            <TextField
              type="url"
              placeholder="https://…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') save()
              }}
            />
            <Button size="sm" variant="primary" onClick={save} disabled={!draft.trim()}>
              Add image
            </Button>
          </div>
        </div>
      </WidgetShell>
    )
  }

  return (
    <WidgetShell title={instance.title} base={def.title} fill bodyClass="ov-image-body">
      {broken ? (
        <div className="ov-image-broken">
          <Text as="p" size="xs" tone="faint">
            Couldn&rsquo;t load this image.
          </Text>
          <Button size="sm" variant="secondary" onClick={clear}>
            Change URL
          </Button>
        </div>
      ) : (
        <>
          <img
            className="ov-image-img"
            src={url}
            alt={instance.title || def.title || 'Widget image'}
            onError={() => setBroken(true)}
          />
          <button type="button" className="ov-image-change" onClick={clear}>
            Change
          </button>
        </>
      )}
    </WidgetShell>
  )
}

// ── Embed ─────────────────────────────────────────────────────────────────────

const EmbedWidget: ComponentType<WidgetContentProps> = ({ instance, def, onPatch }) => {
  const url = (instance.config?.url as string) || ''
  const [draft, setDraft] = useState(url)

  useEffect(() => setDraft(url), [url])

  const save = () => {
    if (!draft.trim()) return
    onPatch({ config: { ...instance.config, url: normalizeUrl(draft) } })
  }
  const clear = () => onPatch({ config: { ...instance.config, url: '' } })

  if (!url) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <div className="ov-embed-setup">
          <Text as="p" size="xs" tone="faint">
            Show an external page here — a Google Sheet, a TradingView chart, a Notion doc.
          </Text>
          <div className="ov-embed-setup-row">
            <TextField
              type="url"
              placeholder="https://…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') save()
              }}
            />
            <Button size="sm" variant="primary" onClick={save} disabled={!draft.trim()}>
              Embed
            </Button>
          </div>
          <Text as="p" size="xs" tone="faint">
            Note: some sites refuse to be embedded (X-Frame-Options).
          </Text>
        </div>
      </WidgetShell>
    )
  }

  return (
    <WidgetShell title={instance.title} base={def.title} fill bodyClass="ov-embed-body">
      {/* This is a USER-SUPPLIED external URL, not Lucius-authored content — a
          different trust boundary than the artifact sandbox below, so it gets its
          own (still conservative) sandbox policy rather than reusing artifactFrame's.
          `allow-same-origin` is DELIBERATELY OMITTED: paired with `allow-scripts`,
          a framed document whose effective origin happens to match this app's own
          (e.g. a careless/malicious URL pointed back at this install) could reach
          across into the parent page with the session cookie's privileges — the
          well-known "sandboxed iframe pointing at its own parent's origin" escape.
          Without it the frame always renders in a unique opaque origin, which is
          the safe default for an arbitrary URL; the cost is that an embed relying
          on same-origin storage/XHR against ITS OWN domain (a minority of embeds)
          may not fully function — an acceptable trade for a widget whose entire
          point is showing someone ELSE's page. */}
      <iframe
        className="ov-embed-frame"
        src={url}
        title={instance.title || def.title || 'Embed'}
        sandbox="allow-scripts allow-popups"
      />
      <button type="button" className="ov-embed-change" onClick={clear}>
        Change URL
      </button>
    </WidgetShell>
  )
}

// ── Lucius custom (lux-html) ─────────────────────────────────────────────────

interface LuxHtmlConfig {
  artifactId?: string
  artifactMeta?: Omit<ArtifactRef, 'id'>
  prompt?: string
}

const DEFAULT_LUX_HTML_PROMPT =
  'Create a dashboard widget artifact via create_artifact — a small, self-contained HTML card I can pin to my Overview dashboard.'

const LuxHtml: ComponentType<WidgetContentProps> = ({ instance, def, onPatch, onAskLucius }) => {
  const cfg = (instance.config || {}) as LuxHtmlConfig
  const [recent, setRecent] = useState<RecentArtifact[] | null>(null)
  const [listError, setListError] = useState(false)
  const [loadingList, setLoadingList] = useState(false)
  const [draft, setDraft] = useState(cfg.prompt || '')

  const loadRecent = useCallback(() => {
    setLoadingList(true)
    setListError(false)
    fetchRecentArtifacts(50)
      .then((rows) => setRecent(rows))
      .catch(() => setListError(true))
      .finally(() => setLoadingList(false))
  }, [])

  useEffect(() => {
    // Only needed while picking — once an artifact is pinned this widget never
    // re-fetches the list again (ArtifactCard fetches the pinned doc itself).
    if (!cfg.artifactId) loadRecent()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg.artifactId])

  const pick = (id: string) => {
    const row = recent?.find((r) => r.id === id)
    if (!row) return
    const meta: Omit<ArtifactRef, 'id'> = {
      title: row.title,
      filename: row.filename,
      artifactType: row.artifactType,
      sizeBytes: row.sizeBytes,
    }
    onPatch({
      config: { ...instance.config, artifactId: row.id, artifactMeta: meta, prompt: draft },
    })
  }

  const clearPick = () =>
    onPatch({ config: { ...instance.config, artifactId: undefined, artifactMeta: undefined } })

  const askLucius = () => {
    const q = draft.trim() || DEFAULT_LUX_HTML_PROMPT
    onPatch({ config: { ...instance.config, prompt: q } })
    onAskLucius?.(q)
  }

  const spark = (
    <span className="ov-lucius-spark">
      <Spark size={15} />
    </span>
  )

  if (cfg.artifactId && cfg.artifactMeta) {
    const ref: ArtifactRef = { id: cfg.artifactId, ...cfg.artifactMeta }
    return (
      <WidgetShell
        title={instance.title}
        base={def.title}
        lead={spark}
        actions={
          <button type="button" className="ov-lux-chip" onClick={clearPick}>
            Change artifact
          </button>
        }
      >
        <ArtifactCard artifact={ref} />
      </WidgetShell>
    )
  }

  return (
    <WidgetShell title={instance.title} base={def.title} lead={spark}>
      <div className="ov-luxhtml-setup">
        <Text as="p" size="xs" tone="faint">
          Pin a Lucius-generated artifact — a chart, a card, a mini dashboard — to this widget.
        </Text>
        {recent && recent.length > 0 && (
          <div className="ov-luxhtml-pick-row">
            <Select
              options={recent.map((r) => ({ value: r.id, label: r.title || r.filename }))}
              placeholder="Pick a recent artifact…"
              value=""
              onChange={(e) => pick(e.target.value)}
            />
            <Button size="sm" variant="ghost" onClick={loadRecent} disabled={loadingList}>
              Refresh
            </Button>
          </div>
        )}
        {recent && recent.length === 0 && !listError && (
          <Text as="p" size="xs" tone="faint">
            No artifacts yet — ask Lucius to build one below.
          </Text>
        )}
        {listError && (
          <Text as="p" size="xs" tone="bad">
            Could not load your recent artifacts.
          </Text>
        )}
        <TextArea
          rows={2}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Describe the widget Lucius should build…"
        />
        <div className="ov-lux-ask-actions">
          <Button
            size="sm"
            variant="primary"
            className="fi-lucius"
            iconLeft={<Spark size={15} ariaHidden />}
            onClick={askLucius}
          >
            Create with Lucius
          </Button>
        </div>
      </div>
    </WidgetShell>
  )
}

export const contentWidgetComponents: Record<string, ComponentType<WidgetContentProps>> = {
  text: TextNote,
  image: ImageWidget,
  embed: EmbedWidget,
  'lux-html': LuxHtml,
}
