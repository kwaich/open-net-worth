// budgetLens/setup/SetupFlow.tsx — setting a budget method up happens in a LARGE MODAL, not
// inline in the pane (owner, 2026-08-23: "I feel we should show these setup screens more like
// a large modal than inline with the view").
//
// Why it reads better as a modal: setting up is a bounded task with its own Cancel/Apply, and
// inline it had no frame — an unconfigured engine has nothing to render behind the form, so
// the page became a title and some controls adrift in whitespace, which is what kept looking
// broken however the spacing was tuned. A modal supplies the frame, dims what is behind, and
// makes Cancel mean something.
//
// The pane itself keeps a small, honest prompt (SetupPrompt) so the lens is never a blank
// screen and the modal is never auto-opened over someone who was only peeking.
import type { ReactNode } from 'react'
import { Button, Modal } from '../../../../ui'

// Owner ask, 2026-09-15 (commit 32d98bf8f): hidden for every budget type for now — temporary,
// KEEP IT PARKED, not deleted, may come back. A bare `const HIDDEN = true` closed over inside
// the component made the whole flow behind it (modal, draft, Apply, toasts) untestable — dead
// code nobody could prove still worked. `_setSetupPromptHiddenForTests` is a seam ONLY a test
// may call (mirrors `lucius/bus.ts`'s `_resetBusForTests`); production never calls it, so the
// prompt stays parked at its real default for every real user.
let HIDDEN = true

export function _setSetupPromptHiddenForTests(hidden: boolean): void {
  HIDDEN = hidden
}

/** The compact "this isn't set up yet" card left in the pane behind the modal. */
export function SetupPrompt({
  name,
  description,
  busy,
  onSetUp,
}: {
  name: string
  description: string
  busy?: boolean
  onSetUp: () => void
}) {
  if (HIDDEN) return null
  return (
    <div className="bgl-setupprompt">
      <div className="bgl-setupprompt__text">
        <h3 className="bgl-setupprompt__title">{name} isn&apos;t set up yet</h3>
        <p className="bgl-setupprompt__sub">{description}</p>
      </div>
      <Button variant="primary" size="dense" loading={busy} onClick={onSetUp}>
        Set up {name}
      </Button>
    </div>
  )
}

export function SetupModal({
  open,
  title,
  subtitle,
  onClose,
  children,
}: {
  open: boolean
  title: string
  subtitle?: string
  onClose: () => void
  children: ReactNode
}) {
  return (
    <Modal open={open} onClose={onClose} title={title} subtitle={subtitle} size="xl">
      {children}
    </Modal>
  )
}
