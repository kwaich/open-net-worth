// The purple "hand this to Lucius" button for an empty screen, shown beside the screen's own
// primary action. A press is the user asking him to start, so the message SENDS in a fresh
// conversation rather than landing in the composer as a draft.
import { Button } from '../ui'
import type { ButtonSize } from '../ui'
import { Spark } from '../../lucius/identity'
import { sendNextPrefill } from '../../state/luciusSendIntent'
import './intro.css'

export interface LuciusCtaProps {
  /** Short, in the screen's own words; "Add with Lucius" / "Set up with Lucius" read best. */
  label?: string
  /** What he is asked, first person, specific to the screen. */
  message: string
  /** Absent in a context with no chat dock: the button then isn't offered at all. */
  onAskLucius?: (text: string) => void
  /** Match the sibling button. */
  size?: ButtonSize
}

export function LuciusCta({
  label = 'Ask Lucius',
  message,
  onAskLucius,
  size = 'sm',
}: LuciusCtaProps) {
  if (!onAskLucius) return null
  return (
    <Button
      variant="primary"
      size={size}
      className="fi-lucius"
      iconLeft={<Spark size={15} ariaHidden />}
      onClick={() => {
        sendNextPrefill()
        onAskLucius(message)
      }}
    >
      {label}
    </Button>
  )
}
