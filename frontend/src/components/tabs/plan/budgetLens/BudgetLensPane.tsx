// budgetLens/BudgetLensPane.tsx — the pane host. Renders the book's ONE budgeting method (the
// home engine); the method menu itself lives in Plan.tsx's sticky masthead (MethodMenu.tsx),
// and the state is shared through useBudgetLens. A genuinely new book gets the first-run
// chooser instead — there is nothing real to show yet (owner, 2026-09-25: the Budget
// Onboarding (cards) design; no previewing a method you haven't switched to).
import type { RegisterFilter } from '../budget'
import { ZeroBasedPane } from './zeroBased/ZeroBasedPane'
import { ProportionalPane } from './proportional/ProportionalPane'
import { GenericEnginePane } from './GenericEnginePane'
import { FirstRun } from './FirstRun'
import { CompareMethodsModal } from './CompareMethodsModal'
import { SwitchMethodModal } from './SwitchMethodModal'
import { OnboardHost } from './OnboardHost'
import type { BudgetLensState } from './useBudgetLens'
import type { BudgetEngineDescriptor } from './types'
import './budgetLens.css'

export function BudgetLensPane({
  lens,
  onAskLucius,
  toast,
  onOpenTxn,
  onOpenRegister,
  onManageCategories,
  categoriesVersion,
}: {
  lens: BudgetLensState
  onAskLucius: (text: string) => void
  toast: (msg: string) => void
  onOpenTxn?: (raw: Record<string, unknown>) => void
  onOpenRegister?: (filter: RegisterFilter) => void
  /** Plan's own "Manage categories" modal — the 50/30/20 toolbar opens the same one. */
  onManageCategories?: () => void
  /** Bumped by Plan.tsx's "Manage categories" modal — forwarded so the budget refreshes. */
  categoriesVersion?: number
}) {
  // An existing budgeter (hasBudgetData) always sees their real budget; the chooser is for a
  // book with nothing to lose, and never comes back once a method is picked.
  const showFirstRun = lens.introLoaded && !lens.introDismissed && !lens.hasBudgetData

  // WHICH pane an engine renders is the DESCRIPTOR's call (`render`, plus a bespoke arm per id).
  // <BudgetTab> is zero-based's OWN pane — Ready to Assign, Age of Money and auto-assign are
  // zero-based concepts — wrapped by ZeroBasedPane so a book that has not started budgeting gets
  // its setup flow instead of an empty table (owner, 2026-08-23).
  function paneFor(e: BudgetEngineDescriptor) {
    if (e.render === 'custom') {
      return (
        <ZeroBasedPane
          descriptor={e}
          onConfiguredChange={lens.refresh}
          onAskLucius={onAskLucius}
          toast={toast}
          onOpenTxn={onOpenTxn}
          onOpenRegister={onOpenRegister}
          categoriesVersion={categoriesVersion}
        />
      )
    }
    if (e.id === 'proportional') {
      return (
        <ProportionalPane
          descriptor={e}
          toast={toast}
          onAskLucius={onAskLucius}
          onManageCategories={onManageCategories}
          onOpenTxn={onOpenTxn}
          onOpenRegister={onOpenRegister}
          onSetup={lens.openOnboarding}
          categoriesVersion={categoriesVersion}
        />
      )
    }
    return (
      <GenericEnginePane
        descriptor={e}
        onConfiguredChange={lens.refresh}
        onAskLucius={onAskLucius}
      />
    )
  }

  // Nothing renders until the engine list has answered: a brief blank beats painting one
  // method's UI and swapping it for another's.
  const home = lens.engines.find((e) => e.id === lens.homeId)

  return (
    <>
      {showFirstRun ? (
        // .bgl-pane owns the top spacing that compensates `.pl-mast-sticky + .pl-tabwrap`'s
        // negative pull — the chooser sits exactly where a method's pane would.
        <div className="bgl-pane">
          <FirstRun onStart={lens.startWith} onLucius={onAskLucius} busy={lens.switching} />
        </div>
      ) : (
        home && (
          <div key={home.id} className="bgl-pane">
            {paneFor(home)}
          </div>
        )
      )}
      <CompareMethodsModal
        open={lens.compareOpen}
        onClose={lens.closeCompare}
        homeId={lens.homeId}
      />
      <SwitchMethodModal lens={lens} />
      <OnboardHost lens={lens} onAskLucius={onAskLucius} />
    </>
  )
}
