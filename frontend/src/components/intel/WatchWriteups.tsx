import { safeHref } from '../../utils/format'
import { OpinionLine } from './OpinionLine'
import type { SignalSource, StoryArc } from './storyMeta'

// The model-written layers on a watch story: the explained alert (a material move, with a
// sourced "why") and Lucius's read (every other live watch story). Both are information for a
// decision, never the decision itself — any lean or suggestion the user's opinion level allows is
// labelled as Lucius's view.

function SourceLinks({ sources }: { sources: SignalSource[] }) {
  return (
    <>
      {sources.map((src, i) => {
        const href = safeHref(src.url)
        return href ? (
          <a
            key={i}
            className="intel-explained-src"
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            title={src.title}
          >
            {src.domain || new URL(href).hostname.replace(/^www\./, '')}
          </a>
        ) : null
      })}
    </>
  )
}

export function ExplainedBlock({ ex }: { ex: NonNullable<StoryArc['explained']> }) {
  return (
    <div className="intel-explained">
      <p className="intel-explained-lead">{ex.what_happened}</p>
      <div className="intel-explained-sec">
        <span className="intel-explained-h">Why</span>
        <p>
          {ex.why} <SourceLinks sources={ex.sources} />
        </p>
      </div>
      <div className="intel-explained-sec">
        <span className="intel-explained-h">What it means for you</span>
        <p>{ex.what_it_means}</p>
      </div>
      {ex.worth_weighing.length > 0 && (
        <div className="intel-explained-sec">
          <span className="intel-explained-h">Worth weighing</span>
          <ul>
            {ex.worth_weighing.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      <OpinionLine opinion={ex} />
    </div>
  )
}

export function ReadBlock({ read }: { read: NonNullable<StoryArc['lucius_read']> }) {
  return (
    <div className="intel-explained intel-read">
      <div className="intel-explained-sec">
        <span className="intel-explained-h">Lucius’s read</span>
        <p>
          {read.text} <SourceLinks sources={read.sources} />
        </p>
      </div>
      {read.watch_for.length > 0 && (
        <div className="intel-explained-sec">
          <span className="intel-explained-h">Watch for</span>
          <ul>
            {read.watch_for.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      <OpinionLine opinion={read} />
    </div>
  )
}
