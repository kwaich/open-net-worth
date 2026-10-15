// Edit an MCP server's connection (spec §3's kebab item — "reopens the setup drawer
// pre-filled").
//
// The drawer it reopens is the ADD FLOW, not a separate form: same four steps, same
// `useMcpAddFlow`, same pinned-footer geometry — handed a saved server to start from. Before
// this, the kebab item opened the directory filtered to MCP, which is a dead end: nowhere in
// the product could an MCP server's URL, command or credential be changed after it was added.
//
// It is its OWN modal rather than a fourth view of `DirectoryModal` because the user did not
// come from the directory and cannot usefully go back to it — the back control returns to the
// detail page they opened the kebab on. One modal is on screen at a time either way, so one
// Escape still closes one thing.

import type { JSX } from 'react'
import { BackTitle, Modal } from './primitives'
import { McpAddFlowBody, McpAddFlowFooter, useMcpAddFlow } from './McpAddFlow'
import type { McpEditTarget } from './mcpConfig'

export function McpEditModal({
  target,
  onClose,
  onSaved,
}: {
  target: McpEditTarget
  /** Back to the detail page, changing nothing. */
  onClose: () => void
  onSaved: (name: string) => void
}): JSX.Element {
  // Mounted only while open (the tab gates it), so the saved server is read once, by the
  // hook's lazy initializer — there is no target-changed effect to keep in sync.
  const flow = useMcpAddFlow({ takenIds: [], editing: target, onSaved: (name) => onSaved(name) })

  // `discardAbandonedOAuthServer` is always a no-op here (`editing` is set for the life of this
  // modal) — wired in anyway so every dismiss path runs the same cleanup `DirectoryModal` does,
  // rather than this modal being the one place that silently relies on the guard elsewhere.
  const close = () => {
    flow.discardAbandonedOAuthServer()
    onClose()
  }

  return (
    <Modal
      title={<BackTitle label={target.name} onClick={close} />}
      size="xl"
      onClose={close}
      footer={<McpAddFlowFooter flow={flow} onCancel={close} />}
    >
      <McpAddFlowBody flow={flow} />
    </Modal>
  )
}
