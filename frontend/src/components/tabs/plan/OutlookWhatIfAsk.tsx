import { Spark } from '../../../lucius/identity'
import { sendNextPrefill } from '../../../state/luciusSendIntent'
import { useSeenIntro } from '../../intro/useSeenIntro'

const WHAT_IFS: { label: string; ask: string }[] = [
  {
    label: 'Retire at 55?',
    ask: 'On my Outlook, what if I retire at 55? Move the sliders so I can see it.',
  },
  {
    label: 'Buy a house in 2028?',
    ask: 'On my Outlook, what if I buy a house in 2028? Move the sliders so I can see it.',
  },
  {
    label: 'Markets fall 30%?',
    ask: 'On my Outlook, what if markets fall 30%? Move the sliders so I can see it.',
  },
]

/** Sits at the top of the What if? panel, above the sliders Lucius moves through the Outlook
 *  screen command. `onPicked` lets a bottom sheet close so the chart is in view when it moves. */
export function OutlookWhatIfAsk({
  onAskLucius,
  onPicked,
}: {
  onAskLucius?: (text: string) => void
  onPicked?: () => void
}) {
  // A starter, not a fixture: once one is tapped the user knows Lucius moves these, so the
  // row goes for good (remembered on the server, like the intros).
  const { unseen, markSeen } = useSeenIntro('plan-whatif-ask')
  if (!onAskLucius || !unseen) return null
  return (
    <div className="pl-whatif-ask" data-tour="setup-outlook">
      <div className="pl-whatif-ask-title">
        <Spark size={13} ariaHidden />
        Ask Lucius to move these for you
      </div>
      <div className="pl-whatif-chips">
        {WHAT_IFS.map((w) => (
          <button
            key={w.label}
            type="button"
            className="pl-whatif-chip"
            onClick={() => {
              markSeen()
              sendNextPrefill()
              onAskLucius(w.ask)
              onPicked?.()
            }}
          >
            <Spark size={11} ariaHidden />
            {w.label}
          </button>
        ))}
      </div>
    </div>
  )
}
