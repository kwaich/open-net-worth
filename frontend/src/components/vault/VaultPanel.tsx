import { useEffect, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { addVaultFile, type VaultItem } from '../../api/vault'
import { refreshVault, useVault } from '../../hooks/useVault'
import { useSessionState } from '../../hooks/useSessionState'
import type { ArtifactRef } from '../../types/chat'
import { FolderLock, Search, Upload, X } from '../../ui/icons'
import { Button } from '../ui/Button'
import { EmptyState, ErrorState } from '../ui/States'
import { VaultItemDetail } from './VaultItemDetail'
import { VaultList, VaultListSkeleton } from './VaultList'
import { markFades, markSideFades } from './fades'
import {
  VAULT_ACCEPT,
  VAULT_FILTERS,
  hasTag,
  matchesQuery,
  tagCounts,
  type VaultFilter,
} from './vaultFormat'
import './vault.css'

// Documents (internally the Vault) — the user's files, statements, receipts and saved artifacts
// in one place.
// A panel, not a tab: desktop docks it in the Lucius tab's right-hand slot (LuciusHome.tsx),
// the phone shows it full screen (LuciusMobile.tsx). Everything in it is a view over records
// that live elsewhere (backend/services/vault.py) — nothing is copied.

function useUploads(onSingle: (item: VaultItem) => void) {
  const [uploading, setUploading] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const upload = async (files: FileList | File[] | null) => {
    const list = Array.from(files ?? [])
    if (!list.length) return
    setError(null)
    setUploading(list.length)
    const added: VaultItem[] = []
    const failed: string[] = []
    for (const file of list) {
      try {
        added.push(await addVaultFile(file))
      } catch (e) {
        failed.push(e instanceof Error ? e.message : `${file.name} could not be added`)
      }
      setUploading((n) => n - 1)
    }
    refreshVault()
    if (failed.length) setError(failed.join(' · '))
    const [only] = added
    if (only && list.length === 1) onSingle(only)
  }
  return { uploading, error, upload }
}

/** The kinds, then the user's tags. A tag narrows whatever kind is chosen; All clears it. */
function VaultFilters({
  filter,
  counts,
  onChange,
  tags,
  tag,
  onTag,
}: {
  filter: VaultFilter
  counts: Record<VaultFilter, number> | undefined
  onChange: (f: VaultFilter) => void
  tags: { tag: string; count: number }[]
  tag: string | null
  onTag: (tag: string | null) => void
}) {
  const rowRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = rowRef.current
    markSideFades(el)
    if (!el || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => markSideFades(el))
    observer.observe(el)
    return () => observer.disconnect()
  }, [counts, tags.length])
  return (
    <div
      ref={rowRef}
      className="vault-filters"
      role="group"
      aria-label="Show"
      onScroll={(e) => markSideFades(e.currentTarget)}
    >
      {VAULT_FILTERS.map((f) => {
        const on = filter === f.key && (f.key !== 'all' || !tag)
        return (
          <button
            key={f.key}
            type="button"
            className={`vault-filter${on ? ' is-on' : ''}`}
            aria-pressed={on}
            onClick={() => {
              onChange(f.key)
              if (f.key === 'all') onTag(null)
            }}
          >
            {f.label}
            {counts && <span className="vault-filter-n">{counts[f.key] ?? 0}</span>}
          </button>
        )
      })}
      {tags.length > 0 && <span className="vault-filter-sep" aria-hidden="true" />}
      {tags.map((t) => {
        const on = tag?.toLowerCase() === t.tag.toLowerCase()
        return (
          <button
            key={t.tag}
            type="button"
            className={`vault-filter${on ? ' is-on' : ''}`}
            aria-pressed={on}
            aria-label={`Tag ${t.tag}`}
            onClick={() => onTag(on ? null : t.tag)}
          >
            <span className="vault-filter-hash" aria-hidden="true">
              #
            </span>
            {t.tag}
            <span className="vault-filter-n">{t.count}</span>
          </button>
        )
      })}
    </div>
  )
}

export function VaultPanel({
  onClose,
  onOpenArtifact,
  onAsked,
  layout = 'side',
  draggableRows = layout !== 'sheet',
}: {
  onClose: () => void
  onOpenArtifact: (artifact: ArtifactRef) => void
  onAsked?: () => void
  layout?: 'side' | 'sheet'
  /** Rows drag into a chat to mention them — wherever a chat is in view beside the panel. Off
   *  by default in a sheet: the phone's covers its chat, and a drag would steal iOS long-press. */
  draggableRows?: boolean
}) {
  const { data, error, initialLoading, refresh } = useVault()
  const [filter, setFilter] = useState<VaultFilter>('all')
  const [query, setQuery] = useState('')
  const [tag, setTag] = useState<string | null>(null)
  // The item being viewed survives a refresh too (sessionStorage), like the panel itself.
  const [selectedId, setSelectedId] = useSessionState('documentsItem')
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const { uploading, error: uploadError, upload } = useUploads((item) => setSelectedId(item.id))

  const all = data?.items ?? []
  const tags = tagCounts(all)
  const items = all.filter(
    (i) =>
      (filter === 'all' || i.kind === filter) && (!tag || hasTag(i, tag)) && matchesQuery(i, query),
  )
  const selected = selectedId ? data?.items.find((i) => i.id === selectedId) : undefined
  // Re-measure when what's listed changes — a filter can make the list fit, or overflow.
  useEffect(() => markFades(bodyRef.current), [items.length, selected])
  const onDrop = (e: DragEvent) => {
    if (!e.dataTransfer.types.includes('Files')) return
    e.preventDefault()
    setDragging(false)
    void upload(e.dataTransfer.files)
  }

  return (
    <section
      className={`vault-panel vault-panel--${layout}${dragging ? ' is-dragging' : ''}`}
      aria-label="Documents"
      // In the phone sheet the dialog opens focused on the panel itself — never on its first
      // button, which lit up "Add files" with a focus ring on every open (owner report).
      {...(layout === 'sheet' ? { tabIndex: -1, 'data-autofocus': true } : {})}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes('Files')) return
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false)
      }}
      onDrop={onDrop}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={VAULT_ACCEPT}
        className="vault-file-input"
        onChange={(e) => {
          void upload(e.target.files)
          e.currentTarget.value = ''
        }}
      />
      {/* The list stays mounted UNDER an open item (inert, hidden from assistive tech), so a
          swipe back reveals it like any iOS stack — and it keeps its scroll position. */}
      <div
        className="vault-list-layer"
        inert={!!selected}
        aria-hidden={selected ? true : undefined}
      >
        <div className="vault-head">
          <span className="vault-head-icon" aria-hidden="true">
            <FolderLock size={18} strokeWidth={1.6} />
          </span>
          <h2 className="vault-title">Documents</h2>
          <Button
            size="sm"
            iconLeft={<Upload size={14} strokeWidth={1.7} />}
            loading={uploading > 0}
            onClick={() => inputRef.current?.click()}
          >
            {uploading > 0 ? `Adding ${uploading}…` : 'Add files'}
          </Button>
          <button
            type="button"
            className="vault-icon-btn"
            onClick={onClose}
            aria-label="Close Documents"
          >
            <X size={18} strokeWidth={1.6} />
          </button>
        </div>
        <div className="vault-tools">
          <label className="vault-search">
            <Search size={14} strokeWidth={1.7} aria-hidden="true" />
            <input
              type="search"
              placeholder="Search documents"
              aria-label="Search documents"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <VaultFilters
            filter={filter}
            counts={data?.counts}
            onChange={setFilter}
            tags={tags}
            tag={tag}
            onTag={setTag}
          />
        </div>
        {uploadError && <p className="vault-error vault-error--bar">{uploadError}</p>}
        <div className="vault-body" ref={bodyRef} onScroll={(e) => markFades(e.currentTarget)}>
          {initialLoading ? (
            <VaultListSkeleton />
          ) : error && !data ? (
            <ErrorState
              title="Couldn't load documents"
              message={error instanceof Error ? error.message : String(error)}
              onRetry={() => void refresh()}
            />
          ) : items.length ? (
            <VaultList items={items} onSelect={setSelectedId} draggable={draggableRows} />
          ) : (
            <EmptyState
              icon={<FolderLock size={22} strokeWidth={1.4} />}
              title={query || tag || filter !== 'all' ? 'Nothing matches' : 'No documents yet'}
              body={
                query || tag || filter !== 'all'
                  ? 'Try another search or filter.'
                  : 'Add tax forms, policies and deeds here. Imported statements, receipts and anything you save from a chat show up on their own.'
              }
            />
          )}
        </div>
        {dragging && <div className="vault-drop">Drop to add to Documents</div>}
      </div>
      {selected && (
        <VaultItemDetail
          item={selected}
          onBack={() => setSelectedId(null)}
          onTag={(t) => {
            // A tag on an item shows everything that has it, whatever its kind.
            setFilter('all')
            setTag(t)
            setSelectedId(null)
          }}
          onClose={onClose}
          onOpenArtifact={onOpenArtifact}
          onAsked={onAsked}
        />
      )}
    </section>
  )
}
