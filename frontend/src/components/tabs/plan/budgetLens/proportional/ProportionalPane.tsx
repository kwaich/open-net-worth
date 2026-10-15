// budgetLens/proportional/ProportionalPane.tsx — the 50/30/20 method's pane. Setting it up is the
// onboarding (income → each group's share, Onboard.tsx), owned by useBudgetLens so a method switch can run it
// BEFORE promoting 50/30/20 (owner, 2026-09-25: the Budget Onboarding (cards) design — it
// replaced the category-mapping review). A 50/30/20 that was never set up shows one card whose
// button opens it; a configured one is the live table (PropTable.tsx). Nothing is fetched while
// unconfigured (C1).
import { Button } from '../../../../ui'
import type { RawTxn } from '../../budgetActivity'
import type { RegisterFilter } from '../../budget'
import type { BudgetEngineDescriptor } from '../types'
import { MethodExplainer } from '../MethodExplainer'
import { PropTable } from './PropTable'

export function ProportionalPane({
  descriptor,
  toast,
  onAskLucius,
  onManageCategories,
  onOpenTxn,
  onOpenRegister,
  onSetup,
  categoriesVersion,
}: {
  descriptor: BudgetEngineDescriptor
  toast: (msg: string) => void
  onAskLucius: (text: string) => void
  onManageCategories?: () => void
  onOpenTxn?: (raw: RawTxn) => void
  onOpenRegister?: (filter: RegisterFilter) => void
  /** Open the onboarding (useBudgetLens.openOnboarding). */
  onSetup: () => void
  /** Bumped by Manage categories — a rename, merge or delete there re-reads the month. */
  categoriesVersion?: number
}) {
  if (!descriptor.configured) {
    return (
      <div className="bgl-setup">
        <div className="bgl-setup__head">
          <h2 className="bgl-setup__title">Set up 50/30/20</h2>
          <p className="bgl-setup__sub">
            Your monthly net income, then a share of it for each of your groups — what's left is
            savings.
          </p>
        </div>
        <MethodExplainer engineId="proportional" />
        <div>
          <Button variant="primary" size="sm" onClick={onSetup}>
            Set up 50/30/20
          </Button>
        </div>
      </div>
    )
  }
  return (
    <PropTable
      toast={toast}
      onLucius={onAskLucius}
      onManageCategories={onManageCategories}
      onOpenTxn={onOpenTxn}
      onOpenRegister={onOpenRegister}
      categoriesVersion={categoriesVersion}
    />
  )
}
