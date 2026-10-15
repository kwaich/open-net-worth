// Rich hover popup for a FUNDING bar segment — lists the individual sources rolled into that
// segment's aggregate total (e.g. "Windfall: S$59,000.00" used to be one opaque number; this
// shows each of the underlying sources by name). Visual shell mirrors the chat mention-detail
// popup (components/chat/lucius/MentionDetail.tsx) — a fixed-position card anchored to the
// hovered segment's own rect — but is a fresh, purpose-built component: this data is already
// in memory (no resolve-on-open network fetch) and the trigger is hover, not click, matching
// this bar's pre-existing (native title=) hover behavior.
import { fBase } from '../../../utils/format'
import { fundingWhen } from './detailFunding'
import type { CoverageBarSource } from './detailFunding'
import type { FundingSource, FundingType } from '../../../types/commitments'
import type { PositionMeta } from './fundingValue'

interface Props {
  anchorRect: DOMRect
  type: FundingType | string
  typeLabel: string
  typeTotal: number
  sources: CoverageBarSource[]
  fundingPlan: FundingSource[]
  positions: Record<string, PositionMeta>
  onMouseEnter: () => void
  onMouseLeave: () => void
}

const POPOVER_WIDTH = 300
const VIEWPORT_MARGIN = 8
// Mirrors commitments.css: head (~44px) + rows padding (~14px) + rows, capped at the CSS
// max-height (240px) since .c2-fund-pop-rows scrolls internally past that — estimating past
// the cap (as if every row added height forever) was pushing the popover far off its anchor
// for any segment with a long source list.
const HEAD_HEIGHT = 44
const ROWS_PADDING = 14
const ROW_HEIGHT = 30
const ROWS_MAX_HEIGHT = 240

export function FundingSourcesPopover({
  anchorRect,
  type,
  typeLabel,
  typeTotal,
  sources,
  fundingPlan,
  positions,
  onMouseEnter,
  onMouseLeave,
}: Props) {
  const rowsHeight = Math.min(sources.length * ROW_HEIGHT, ROWS_MAX_HEIGHT)
  const estHeight = HEAD_HEIGHT + ROWS_PADDING + rowsHeight
  const placeBelow = anchorRect.bottom + 8 + estHeight < window.innerHeight
  const top = placeBelow
    ? anchorRect.bottom + 8
    : Math.max(VIEWPORT_MARGIN, anchorRect.top - estHeight - 8)
  const centerX = anchorRect.left + anchorRect.width / 2
  const left = Math.min(
    Math.max(VIEWPORT_MARGIN, centerX - POPOVER_WIDTH / 2),
    window.innerWidth - POPOVER_WIDTH - VIEWPORT_MARGIN,
  )

  return (
    <div
      className="c2-fund-pop"
      style={{ top, left, width: POPOVER_WIDTH }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <div className="c2-fund-pop-head">
        <span className={'c2-fund-dot ' + type} />
        <span>{typeLabel}</span>
        <span className="money">{fBase(typeTotal)}</span>
      </div>
      <div className="c2-fund-pop-rows">
        {sources.map((s) => {
          const f = fundingPlan.find((x) => x.id === s.id)
          const pos = f?.ref_position_id ? positions[f.ref_position_id] : undefined
          return (
            <div key={s.id} className="c2-fund-pop-row">
              <span className={'c2-fund-dot ' + type} />
              <span className="lbl">
                {s.label}
                {f && <span className="sub">{fundingWhen(f, pos)}</span>}
              </span>
              <span className="amt money">{fBase(s.base)}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
