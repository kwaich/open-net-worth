// budgetLens/SwitchMethodModal.tsx — the ONE confirmation every method switch goes through
// (the method menu stages it via useBudgetLens.requestSwitch; nothing POSTs a switch directly).
// Copy ported from the Budget Onboarding (cards) design's switch modal (owner, 2026-09-25):
// switching is non-destructive — the outgoing method's setup is kept, so it says so.
import { Button, Modal } from '../../../ui'
import { methodById } from './methodsData'
import type { BudgetLensState } from './useBudgetLens'

export function SwitchMethodModal({ lens }: { lens: BudgetLensState }) {
  const to = methodById(lens.switchTo)
  const fromName =
    methodById(lens.homeId)?.name ?? lens.engines.find((e) => e.id === lens.homeId)?.name
  // "Back" — this method was set up before, so its own setup is waiting as it was left.
  const back = !!to && !!lens.engines.find((e) => e.id === to.id)?.configured
  return (
    <Modal
      open={!!to}
      onClose={lens.cancelSwitch}
      size="sm"
      title={to ? (back ? 'Switch back to ' : 'Switch to ') + to.name + '?' : ''}
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={lens.cancelSwitch}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" loading={lens.switching} onClick={lens.confirmSwitch}>
            Switch
          </Button>
        </>
      }
    >
      {to && (
        <div className="bo-switch">
          <p className="bo-switch-p">
            {back ? (
              <>
                Your{' '}
                {to.id === 'proportional' ? 'split and caps are' : 'assignments and targets are'} as
                you left them. Your {fromName} setup stays saved — you can switch back any time.
              </>
            ) : (
              <>
                Your envelopes and categories come with you.{' '}
                {to.id === 'zero_based'
                  ? 'Nothing is assigned yet.'
                  : 'You’ll set your income and each group’s share next.'}{' '}
                Your {fromName} setup stays saved — switch back any time.
              </>
            )}
          </p>
        </div>
      )}
    </Modal>
  )
}
