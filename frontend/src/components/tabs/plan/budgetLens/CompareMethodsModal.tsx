// budgetLens/CompareMethodsModal.tsx — "Compare methods…" from the method menu: the first-run
// cards again, read-only, with the current method marked. Switching lives in the menu, never
// here (owner, 2026-09-25 — the Budget Onboarding (cards) design's CompareModal, readOnly).
import { useState } from 'react'
import { Modal } from '../../../ui'
import { MethodCard } from './MethodCard'
import { METHODS } from './methodsData'

export function CompareMethodsModal({
  open,
  onClose,
  homeId,
}: {
  open: boolean
  onClose: () => void
  homeId: string
}) {
  const [more, setMore] = useState(false)
  return (
    <Modal open={open} onClose={onClose} title="Compare budgeting methods" size="xl">
      <div className="bo-cards">
        {METHODS.map((m) => (
          <MethodCard
            key={m.id}
            m={m}
            mode="static"
            current={m.id === homeId}
            more={more}
            onMore={() => setMore((o) => !o)}
          />
        ))}
      </div>
    </Modal>
  )
}
