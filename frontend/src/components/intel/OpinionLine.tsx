import { NOT_ADVICE } from './monitoringConfig'
import type { BriefOpinion } from '../../types/intelDigest'

const LEAN_TONE: Record<NonNullable<BriefOpinion['lean']>, string> = {
  bullish: 'intel-opinion-lean--bull',
  bearish: 'intel-opinion-lean--bear',
  neutral: '',
}

/** Lucius's lean and suggestion, when the user's opinion level (Settings › Intelligence) allows
 *  them — always labelled as Lucius's view, never advice. Renders nothing at the information
 *  level, where neither field is written. */
export function OpinionLine({ opinion }: { opinion: BriefOpinion | null | undefined }) {
  if (!opinion || (!opinion.lean && !opinion.suggestion)) return null
  return (
    <div className="intel-opinion">
      {opinion.lean && (
        <p className="intel-opinion-row">
          <span className={`intel-opinion-lean ${LEAN_TONE[opinion.lean]}`.trim()}>
            Leans {opinion.lean}
          </span>
          {opinion.lean_reason && <span>{opinion.lean_reason}</span>}
        </p>
      )}
      {opinion.suggestion && (
        <p className="intel-opinion-row">
          <span className="intel-opinion-label">Lucius suggests</span>
          <span>{opinion.suggestion}</span>
        </p>
      )}
      <p className="intel-opinion-note">{NOT_ADVICE}</p>
    </div>
  )
}
