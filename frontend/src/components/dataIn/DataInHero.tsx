// The shared "you have no accounts yet" empty view. Reused by Dashboard and Accounts so the
// first-run moment reads the same on both, and like every other empty view in the app.

import { Button, Cluster, EmptyState } from '../ui'
import { Landmark } from '../../ui/icons'
import { LuciusCta } from '../intro/LuciusCta'

export interface DataInHeroProps {
  title: string
  body: string
  /** The screen's own way in; Lucius's button follows it. */
  onAddAccount: () => void
  /** What Lucius is asked when his button is pressed. */
  luciusMessage: string
  /** Absent in a context with no chat dock: the Lucius button is then not offered. */
  onAskLucius?: (text: string) => void
}

export function DataInHero({
  title,
  body,
  onAddAccount,
  luciusMessage,
  onAskLucius,
}: DataInHeroProps) {
  return (
    <EmptyState
      fill
      icon={<Landmark size={28} strokeWidth={1.5} aria-hidden="true" />}
      title={title}
      body={body}
      action={
        <Cluster gap="--space-3" justify="center">
          <Button variant="primary" size="sm" onClick={onAddAccount}>
            Add an account myself
          </Button>
          <LuciusCta label="Add with Lucius" message={luciusMessage} onAskLucius={onAskLucius} />
        </Cluster>
      }
    />
  )
}
