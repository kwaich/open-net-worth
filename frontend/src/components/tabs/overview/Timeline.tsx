// Overview v2 — the reusable one-axis Timeline primitive: a fixed axis Strip +
// a scrollable List, composed so a widget can pin the axis outside its
// scrolling body (via WidgetShell's `subhead` slot). Ported from the handoff's
// `timeline.jsx`. Reusable for anything laid out on one time axis (calendar,
// upcoming, dividend schedule).

export interface TimelineEvent {
  pos: number
  color?: string
  dateLabel: string
  label: string
  sub?: string
  amount?: string
  past?: boolean
}

export interface TimelineTick {
  pos: number
  label: string
}

function pctOf(axis: [number, number]) {
  const lo = axis[0]
  const hi = axis[1]
  const span = hi - lo || 1
  return (p: number) => Math.max(0, Math.min(100, ((p - lo) / span) * 100))
}

export function TimelineStrip({
  events,
  axis,
  ticks,
}: {
  events: TimelineEvent[]
  axis: [number, number]
  ticks?: TimelineTick[]
}) {
  const lo = axis[0]
  const hi = axis[1]
  const pct = pctOf(axis)
  const onAxis = (events || []).filter((e) => e.pos >= lo && e.pos <= hi)
  return (
    <div className="ov-tl-strip" aria-hidden="true">
      <div className="ov-tl-track" />
      {onAxis.map((e, i) => (
        <span
          key={i}
          className="ov-tl-dot"
          // Inset by the dot's radius so a dot at "today" or "+30d" stays inside the strip.
          style={{ left: `calc(4px + (100% - 8px) * ${pct(e.pos) / 100})`, background: e.color }}
          title={e.label}
        />
      ))}
      {(ticks || []).map((t, i) => {
        const style =
          t.pos <= lo
            ? { left: 0 }
            : t.pos >= hi
              ? { right: 0 }
              : { left: pct(t.pos) + '%', transform: 'translateX(-50%)' }
        return (
          <span key={'t' + i} className="ov-tl-tick" style={style}>
            {t.label}
          </span>
        )
      })}
    </div>
  )
}

export function TimelineList({
  events,
  axis,
  extra,
  moreLabel,
}: {
  events: TimelineEvent[]
  axis?: [number, number]
  extra?: number
  moreLabel?: string
}) {
  const hi = axis ? axis[1] : Infinity
  return (
    <div className="ov-tl-list">
      {(events || []).map((e, i) => (
        <div
          className={
            'ov-tl-row' +
            (i === 0 ? ' first' : '') +
            (e.past ? ' past' : '') +
            (e.pos > hi ? ' far' : '')
          }
          key={i}
        >
          <span className="ov-tl-date">{e.dateLabel}</span>
          <span className="ov-tl-mk" style={{ background: e.color }} />
          <span className="ov-tl-lbl">
            {e.label}
            {e.sub ? <span> • {e.sub}</span> : null}
          </span>
          {e.amount && <span className="ov-tl-amt money">{e.amount}</span>}
        </div>
      ))}
      {!!extra && extra > 0 && (
        <div className="ov-more">
          +{extra} more{moreLabel ? ' ' + moreLabel : ''}
        </div>
      )}
    </div>
  )
}

export function Timeline({
  events,
  axis,
  ticks,
  moreLabel,
  extra,
}: {
  events: TimelineEvent[]
  axis: [number, number]
  ticks?: TimelineTick[]
  moreLabel?: string
  extra?: number
}) {
  return (
    <div className="ov-tl">
      <TimelineStrip events={events} axis={axis} ticks={ticks} />
      <TimelineList events={events} axis={axis} extra={extra} moreLabel={moreLabel} />
    </div>
  )
}
