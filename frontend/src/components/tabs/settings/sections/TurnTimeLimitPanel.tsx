// Settings → AI Models → Turn time limit. A slow model provider can take minutes per call, so
// how long a chat reply or an Automation run may take is the owner's call, up to no limit.
import {
  TURN_TIME_LIMIT_KEY,
  getTurnTimeLimit,
  setTurnTimeLimit,
  turnTimeLimitLabel,
} from '../../../../api/turnTimeLimit'
import type { TurnTimeLimit } from '../../../../api/turnTimeLimit'
import { setCachedQueryData, useCachedFetchState } from '../../../../state/queryCache'
import { Field, Panel, Select } from '../ds'
import type { SectionProps } from '../ds'

export function TurnTimeLimitPanel({ toast }: Pick<SectionProps, 'toast'>) {
  const { data: loaded, error } = useCachedFetchState<TurnTimeLimit>(
    TURN_TIME_LIMIT_KEY,
    getTurnTimeLimit,
    {
      staleTime: 0,
    },
  )

  // Anything without the choices list (an older server, a stub) counts as not loaded.
  const data = Array.isArray(loaded?.choices) ? loaded : undefined

  // Optimistic: the pick shows at once; only a failed save puts the old value back.
  const save = async (seconds: number) => {
    if (!data) return
    const before = data
    setCachedQueryData<TurnTimeLimit>(TURN_TIME_LIMIT_KEY, { ...before, seconds })
    try {
      await setTurnTimeLimit(seconds)
      toast('Turn time limit: ' + turnTimeLimitLabel(seconds))
    } catch {
      setCachedQueryData<TurnTimeLimit>(TURN_TIME_LIMIT_KEY, before)
      toast('Could not save — still ' + turnTimeLimitLabel(before.seconds))
    }
  }

  return (
    <Panel
      title="Turn time limit"
      sub="How long Lucius may work on one reply or Automation run before it is stopped"
    >
      <div className="rd-formgrid">
        <Field
          label="Stop a turn after"
          help="Raise this if a slow model keeps getting stopped mid-task. With no limit, a turn runs until it finishes or you press Stop."
        >
          {data ? (
            <Select
              value={String(data.seconds)}
              onChange={(e) => void save(Number(e.target.value))}
            >
              {data.choices.map((s) => (
                <option key={s} value={String(s)}>
                  {turnTimeLimitLabel(s, data.default_seconds)}
                </option>
              ))}
            </Select>
          ) : (
            <span className="rd-help">
              {error ? 'Couldn’t load — reload the page' : 'Loading…'}
            </span>
          )}
        </Field>
      </div>
    </Panel>
  )
}
