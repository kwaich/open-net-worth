import { useState } from 'react'
import { FormField, NumberField, Panel, TextArea } from '../ui'

// "Why you're watching" — the thesis, buy zone and fair value the Watching pulse turns into
// rules ("Entered your buy zone", "12% below your fair value", "Earnings in 3 days — check your
// thesis") and the explained alert ties its "what it means for you" to. Saved on the watch item.

export interface WatchIntent {
  thesis?: string
  buy_below?: number
  buy_above?: number
  fair_value?: number
}

interface Props {
  sym: string
  ccy: string
  intent: WatchIntent | null
  /** Saves the whole intent ({} clears it); rejects with the server's message. */
  onSave: (next: WatchIntent) => Promise<void>
}

const num = (s: string): number | undefined => {
  const v = parseFloat(s.replace(/,/g, ''))
  return Number.isFinite(v) && v > 0 ? v : undefined
}
const str = (v: number | undefined) => (v == null ? '' : String(v))

export function WatchIntentSection({ sym, ccy, intent, onSave }: Props) {
  const [thesis, setThesis] = useState(intent?.thesis ?? '')
  const [below, setBelow] = useState(str(intent?.buy_below))
  const [above, setAbove] = useState(str(intent?.buy_above))
  const [fair, setFair] = useState(str(intent?.fair_value))
  const [savedJson, setSavedJson] = useState(JSON.stringify(intent ?? {}))
  const [state, setState] = useState<'idle' | 'saving' | 'saved'>('idle')
  const [error, setError] = useState<string | null>(null)

  const next: WatchIntent = {
    ...(thesis.trim() ? { thesis: thesis.trim() } : {}),
    ...(num(below) != null ? { buy_below: num(below) } : {}),
    ...(num(above) != null ? { buy_above: num(above) } : {}),
    ...(num(fair) != null ? { fair_value: num(fair) } : {}),
  }
  const zoneError =
    next.buy_above != null && next.buy_below == null
      ? 'Set the top of the zone ("buy at or under") first.'
      : next.buy_above != null && next.buy_below != null && next.buy_above >= next.buy_below
        ? 'The floor must be below the top of the zone.'
        : null

  // Like the rest of this dialog (mute, news sources): a field commits when you leave it —
  // no Save button that would only cover part of the screen.
  async function commit() {
    const json = JSON.stringify(next)
    if (zoneError || json === savedJson) return
    setState('saving')
    setError(null)
    try {
      await onSave(next)
      setSavedJson(json)
      setState('saved')
    } catch (e) {
      setState('idle')
      setError(e instanceof Error && e.message ? e.message : 'Could not save — try again.')
    }
  }
  const blur = () => void commit()

  return (
    <Panel
      title="Why you're watching"
      subtitle={`Turns ${sym}'s price moves into alerts about your plan, not just the price.`}
      badge={
        state === 'saved' ? (
          <span className="watch-intent-saved">Saved</span>
        ) : state === 'saving' ? (
          <span className="watch-intent-saved">Saving…</span>
        ) : undefined
      }
    >
      <div className="watch-intent-grid">
        <FormField
          label="Thesis"
          help="One line: why you'd own it."
          span="full"
          htmlFor="wi-thesis"
        >
          <TextArea
            id="wi-thesis"
            rows={2}
            maxLength={280}
            value={thesis}
            placeholder="e.g. AI accelerator share gains keep compounding"
            onChange={(e) => setThesis(e.target.value)}
            onBlur={blur}
          />
        </FormField>
        <FormField
          label={`Buy at or under (${ccy})`}
          htmlFor="wi-below"
          error={zoneError ?? undefined}
        >
          <NumberField
            id="wi-below"
            inputMode="decimal"
            value={below}
            onChange={(e) => setBelow(e.target.value)}
            onBlur={blur}
            invalid={!!zoneError}
          />
        </FormField>
        <FormField label={`…but not under (${ccy})`} help="Optional floor." htmlFor="wi-above">
          <NumberField
            id="wi-above"
            inputMode="decimal"
            value={above}
            onChange={(e) => setAbove(e.target.value)}
            onBlur={blur}
          />
        </FormField>
        <FormField
          label={`Fair value (${ccy})`}
          help="What you think it's worth."
          htmlFor="wi-fair"
        >
          <NumberField
            id="wi-fair"
            inputMode="decimal"
            value={fair}
            onChange={(e) => setFair(e.target.value)}
            onBlur={blur}
          />
        </FormField>
      </div>
      {error && <div className="pa-err watch-intent-err">{error}</div>}
    </Panel>
  )
}
