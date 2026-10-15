import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import '../../src/index.tokens.generated.css'
import '../../src/index.css'
import { DeskStatusBar } from '../../src/components/intel/DeskStatusBar'
import { IntelCardHead } from '../../src/components/intel/IntelCardHead'
import { PulseSortMenu } from '../../src/components/intel/PulseSortMenu'
import type { PulseSort } from '../../src/components/intel/pulseHelp'
import type { StoryArc } from '../../src/components/intel/storyMeta'
import { Sparkles } from '../../src/ui/icons'

const signals = (length: number) =>
  Array.from({ length }, (_, i) => ({ id: `synthetic-${i}` }) as StoryArc)
const counts = {
  action_needed: signals(17),
  needs_recheck: signals(27),
  active: signals(18),
  monitoring: signals(9),
}

export function Fixture() {
  const [sort, setSort] = useState<PulseSort>('attention')
  const [archive, setArchive] = useState(false)
  const [unread, setUnread] = useState(signals(69))
  const [focused, setFocused] = useState('')
  const scan = new URLSearchParams(location.search).has('scan')
  return (
    <main className="intel-tab" style={{ padding: 16, maxWidth: 1200, margin: 'auto' }}>
      <p style={{ marginBottom: 16 }}>EXAMPLE · NOT YOUR DATA</p>
      <section className="intel-brief-card">
        <IntelCardHead
          icon={<Sparkles size={13} />}
          title={scan ? 'Watchlist pulse' : 'Holdings brief'}
          ask={{ label: 'Ask Lucius about this brief', onAsk: () => setFocused('Lucius') }}
        />
        <div className="intel-brief-body">
          <p className="intel-brief-narrative">
            Your book is +S$1,010.71 (+0.58%) today — EXAMPLE led (+S$555.88). You're 1.2 pts behind
            SPY 1M.
          </p>
        </div>
        <div className="intel-desk">
          <DeskStatusBar
            counts={counts}
            unread={unread}
            archiveCount={81}
            showArchive={archive}
            onToggleArchive={() => setArchive((v) => !v)}
            onMarkAllRead={() => setUnread([])}
            onFocusStory={setFocused}
            newsScannedAt={scan ? null : undefined}
            toolbar={<PulseSortMenu value={sort} onChange={setSort} />}
          />
          <div className="intel-desk-group">
            <div className="intel-desk-group-head">Across your portfolio</div>
            <p>{archive ? 'Archived example stories' : 'Example holdings'}</p>
            <output aria-label="Focused story">{focused}</output>
          </div>
        </div>
      </section>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<Fixture />)
