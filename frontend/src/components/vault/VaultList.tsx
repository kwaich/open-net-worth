import { useState } from 'react'
import { ActionMenu } from '../ui/ActionMenu'
import { Skeleton } from '../ui/Skeleton'
import type { ReactNode } from 'react'
import type { VaultItem } from '../../api/vault'
import { EntityRow } from '../../lucius/EntityRow'
import {
  Box,
  Code2,
  FileText,
  ImageIcon,
  Landmark,
  Lock,
  MoreVertical,
  Receipt,
} from '../../ui/icons'
import { ItemActionDialog, type ItemAction } from './ItemActionDialog'
import { isImage, removalActions, vaultSublabel } from './vaultFormat'

function PhotoThumb({ item, fallback }: { item: VaultItem; fallback: ReactNode }) {
  const [failed, setFailed] = useState(false)
  if (failed) return fallback
  return (
    <img
      className="vault-kind-thumb"
      src={`/api/vault/items/${encodeURIComponent(item.id)}/thumb`}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
    />
  )
}

export function VaultKindIcon({
  item,
  size = 17,
  thumbnail = false,
}: {
  item: VaultItem
  size?: number
  /** A photo shows a small server-made thumbnail instead of the generic image icon. */
  thumbnail?: boolean
}) {
  let icon: ReactNode
  if (item.kind === 'statement') icon = <Landmark size={size} strokeWidth={1.5} />
  else if (item.kind === 'receipt') icon = <Receipt size={size} strokeWidth={1.5} />
  else if (item.kind === 'artifact') icon = <Code2 size={size} strokeWidth={1.5} />
  else if (item.kind === 'asset' && !isImage(item)) icon = <Box size={size} strokeWidth={1.5} />
  else if (isImage(item)) icon = <ImageIcon size={size} strokeWidth={1.5} />
  else icon = <FileText size={size} strokeWidth={1.5} />
  return (
    <span className={`vault-kind-icon vault-kind-icon--${item.kind}`} aria-hidden="true">
      {thumbnail && isImage(item) ? <PhotoThumb item={item} fallback={icon} /> : icon}
    </span>
  )
}

// The descriptor's own keys (backend/services/mentions/descriptors/vault.py) — metadata only.
function vaultFields(item: VaultItem): Record<string, string | number | null | undefined> {
  return {
    id: item.id,
    title: item.title,
    kind: item.kind,
    filename: item.filename,
    year: item.year,
    added_at: item.added_at,
    account_name: item.account_name,
    statement_date: item.statement_date,
    transaction_date: item.transaction_date,
    private: item.private ? 'yes' : undefined,
  }
}

/** A row's ⋮ — Rename, the note, the tags, then the removals this kind allows: the same dialogs
 *  its page's Manage opens (owner: "should we have kebab menu for quick delete, rename?"). */
function RowMenu({ item }: { item: VaultItem }) {
  const [action, setAction] = useState<ItemAction | null>(null)
  return (
    <>
      <ActionMenu
        className="vault-row-menu"
        label={`More actions for ${item.title}`}
        title={item.title}
        trigger={<MoreVertical size={18} aria-hidden="true" />}
        items={[
          { label: 'Rename', onSelect: () => setAction('rename') },
          { label: item.note ? 'Edit note' : 'Add a note', onSelect: () => setAction('note') },
          {
            label: item.tags?.length ? 'Edit tags' : 'Add tags',
            onSelect: () => setAction('tags'),
          },
          ...removalActions(item).map((removal) => ({
            label: removal.label,
            tone: removal.keeps ? ('default' as const) : ('danger' as const),
            onSelect: () => setAction(removal),
          })),
        ]}
      />
      {action && <ItemActionDialog item={item} action={action} onClose={() => setAction(null)} />}
    </>
  )
}

export function VaultList({
  items,
  onSelect,
  draggable = true,
}: {
  items: VaultItem[]
  onSelect: (id: string) => void
  /** Drag a row into the chat to mention it. Off in the phone sheet: it covers the chat, so a
   *  drag has nowhere visible to land, and on iOS it steals the long-press (owner report). */
  draggable?: boolean
}) {
  return (
    <ul className="vault-list">
      {items.map((item) => (
        <li key={item.id} className="vault-list-item">
          <EntityRow
            as="button"
            htmlType="button"
            type="document"
            id={item.id}
            label={item.title}
            item={item}
            toFields={vaultFields}
            sublabel={vaultSublabel(item)}
            search={[item.filename, item.account_name].filter(Boolean).join(' ')}
            className="vault-row"
            draggable={draggable}
            onClick={() => onSelect(item.id)}
          >
            <VaultKindIcon item={item} thumbnail />
            <span className="vault-row-text">
              <span className="vault-row-title">{item.title}</span>
              <span className="vault-row-sub">{vaultSublabel(item)}</span>
            </span>
            {item.private && (
              <span className="vault-row-lock" title="Private — Lucius can't read it">
                <Lock size={14} strokeWidth={1.7} aria-label="Private" />
              </span>
            )}
          </EntityRow>
          <RowMenu item={item} />
        </li>
      ))}
    </ul>
  )
}

const TITLE_W = [62, 48, 70, 55, 66, 44, 58, 52]
const SUB_W = [38, 30, 42, 34, 36, 28, 40, 32]

/** The list while it loads: rows in the list's own shape (icon tile, title, second line), so
 *  the real rows land in the same slots (owner: "we are not having skeleton loading?"). */
export function VaultListSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="vault-list" role="status" aria-label="Loading documents" aria-busy="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="vault-row vault-row--skeleton">
          <Skeleton width={36} height={36} radius="var(--radius-md)" />
          <span className="vault-row-text">
            <Skeleton width={`${TITLE_W[i % TITLE_W.length]}%`} height={14} radius={4} />
            <Skeleton width={`${SUB_W[i % SUB_W.length]}%`} height={11} radius={4} />
          </span>
        </div>
      ))}
    </div>
  )
}
