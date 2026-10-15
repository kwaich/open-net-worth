// Per-row actions for a conversation, shared by the Lucius sidebar (ConversationMenu) and
// the full list (ConversationBrowser) so a row behaves the same in both places.
//
// Rename came back here. It existed on the old header dropdown and was lost when that
// dropdown became the sidebar — a chat is titled from its first message, so without rename
// the only way to fix a bad title was to start over.
//
// The actions live behind a kebab rather than a row of hover icons: a bare ✕ on every row
// is a delete you can hit by accident and nothing else, and there is no room to add a
// second icon beside it on a 320px-wide panel.

import { useEffect, useRef, useState } from 'react'
import { MoreHorizontal } from '../../ui/icons'
import { ActionMenu } from '../ui/ActionMenu'
import { ConfirmDialog } from '../ui/ConfirmDialog'
import type { ConversationMeta } from '../../hooks/useConversations'
import type { ConvRowStatus } from '../../types/chat'

/** An Automation's conversation is owned by its Automation — DELETE /api/chat/{id} refuses
 *  it with a 409 ("delete the Automation to remove its run history"). Offering the item
 *  anyway would be a button that silently does nothing, so runs get rename only. */
function isDeletable(conv: ConversationMeta): boolean {
  return conv.source !== 'automation'
}

export function ConversationRowMenu({
  conv,
  status,
  onStartRename,
  onDelete,
  onStop,
  className,
  onMenuOpenChange,
}: {
  conv: ConversationMeta
  /** concurrent-conversations-ui-spec.md §5.1.3: a `working` row gets a quick Stop,
   *  prepended above Rename. Deliberately NOT offered on `needs_you` — approving or
   *  denying a money write blind, from a list row without seeing the change, is exactly
   *  the shortcut a finance app should not offer. Optional so every existing caller that
   *  doesn't know a row's status yet keeps working unchanged. */
  status?: ConvRowStatus
  onStartRename: (id: string) => void
  onDelete?: (id: string) => void
  onStop?: (id: string) => void
  className?: string
  /** Fires when this row has UI-in-flight that an ancestor must not close out from under —
   *  the kebab menu being open, OR (below) the delete confirm dialog being up. A
   *  hover-triggered ancestor (e.g. LuciusHistoryHoverList) uses it to suppress its own
   *  mouseleave-close while either is showing: both are document.body portals (ActionMenu.tsx
   *  / ui/Modal.tsx), outside this row's own DOM subtree, so moving the cursor toward one
   *  otherwise reads as "left the flyout." Optional; omitted everywhere else this row renders. */
  onMenuOpenChange?: (open: boolean) => void
}) {
  const [kebabOpen, setKebabOpen] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  // ONE derived signal, not two independently-timed calls. ActionMenu's own close (on
  // selecting an item) and this row setting `confirmingDelete` both happen inside the SAME
  // click, but ActionMenu's onOpenChange(false) fires from a React state update while
  // `onSelect` fires as a plain synchronous call in between — there is no guaranteed order
  // between them. Calling `onMenuOpenChange` directly from each spot let the LATER-committing
  // one silently overwrite the earlier one with a stale value (open→confirm's "true" got
  // clobbered back to "false" moments later), closing the ancestor flyout out from under an
  // OPEN confirm dialog — real-browser-only; jsdom's synthetic event timing never reproduced
  // it. An effect reacting to the two states TOGETHER sees them post-commit, so it only ever
  // reports the actual final combined value.
  useEffect(() => {
    onMenuOpenChange?.(kebabOpen || confirmingDelete)
  }, [kebabOpen, confirmingDelete, onMenuOpenChange])

  const items = [
    ...(status === 'working' && onStop ? [{ label: 'Stop', onSelect: () => onStop(conv.id) }] : []),
    { label: 'Rename', onSelect: () => onStartRename(conv.id) },
    ...(isDeletable(conv) && onDelete
      ? [
          {
            label: 'Delete',
            tone: 'danger' as const,
            onSelect: () => setConfirmingDelete(true),
          },
        ]
      : []),
  ]

  return (
    // No stopPropagation here: the row is a plain wrapper (the button that switches
    // conversation is a sibling), and swallowing pointerdown would keep ActionMenu's
    // document-level outside-click listener from ever seeing it — so opening one row's menu
    // would leave the previous row's menu open behind it.
    <span className={`conv-row-kebab${className ? ` ${className}` : ''}`}>
      <ActionMenu
        items={items}
        label={conv.title}
        trigger={<MoreHorizontal size={15} aria-hidden="true" />}
        onOpenChange={setKebabOpen}
      />
      <ConfirmDialog
        data={
          confirmingDelete
            ? {
                title: 'Delete conversation?',
                body: `Delete "${conv.title?.trim() || 'this conversation'}"? This can't be undone.`,
                confirmLabel: 'Delete',
                danger: true,
              }
            : null
        }
        onConfirm={() => {
          setConfirmingDelete(false)
          onDelete?.(conv.id)
        }}
        onCancel={() => setConfirmingDelete(false)}
      />
    </span>
  )
}

/**
 * The row's title, in place, as an input. Enter or blur commits, Escape cancels.
 *
 * Blur-commits rather than blur-cancels because the mouse leaving the row is the common way
 * to finish typing, and losing the edit there reads as the app throwing the rename away.
 */
export function ConversationRenameInput({
  initial,
  onCommit,
  onCancel,
  className,
}: {
  initial: string
  onCommit: (title: string) => void
  onCancel: () => void
  className: string
}) {
  const [value, setValue] = useState(initial)
  const inputRef = useRef<HTMLInputElement>(null)
  // Escape must not also commit via the blur that follows it.
  const cancelled = useRef(false)

  useEffect(() => {
    inputRef.current?.select()
  }, [])

  return (
    <input
      ref={inputRef}
      className={className}
      value={value}
      maxLength={80}
      aria-label="Conversation title"
      autoFocus
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault()
          onCommit(value)
        } else if (event.key === 'Escape') {
          event.preventDefault()
          cancelled.current = true
          onCancel()
        }
      }}
      onBlur={() => {
        if (cancelled.current) return
        onCommit(value)
      }}
    />
  )
}
