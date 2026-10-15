import { Button, Cluster, EmptyState } from '../ui'
import { Globe } from '../../ui/icons'
import { LuciusCta } from '../intro/LuciusCta'

const TOPICS_MESSAGE =
  'Help me follow topics and macro themes. Ask what I care about, like rates, a currency or a ' +
  'sector, then follow them and run a scan. Ask me one thing at a time.'

// Topics & macro with no stories at all yet — says what will land here and offers the one next
// step. (My holdings and the list tabs never reach this: their pulse rows are the tab.)
export function MacroColdStart({
  scanning,
  onScan,
  onAskLucius,
}: {
  scanning: boolean
  onScan: () => void
  onAskLucius?: (text: string) => void
}) {
  return (
    <div className="intel-coldstart">
      <EmptyState
        fill
        icon={<Globe size={28} strokeWidth={1.5} aria-hidden="true" />}
        title="No topic or macro stories yet"
        body="Follow a topic above, then scan — the themes you follow, central banks, currencies and the economy land here."
        action={
          <Cluster gap="--space-3" justify="center">
            <Button variant="primary" size="sm" onClick={onScan} disabled={scanning}>
              {scanning ? 'Scanning…' : 'Scan now'}
            </Button>
            <LuciusCta
              label="Follow with Lucius"
              message={TOPICS_MESSAGE}
              onAskLucius={onAskLucius}
            />
          </Cluster>
        }
      />
    </div>
  )
}
