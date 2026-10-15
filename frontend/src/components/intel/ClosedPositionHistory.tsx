import { LotsPanel } from '../shared/LotsPanel'
import type { WriteTarget } from '../shared/LotsPanel'
import type { AssetPosition } from '../../types/assetDetail'

interface Props {
  position: AssetPosition
  symbol: string
  instrumentId: string
  onChanged?: () => void
}

function writeTargetsOf(position: AssetPosition): WriteTarget[] {
  return position.per_account
    .filter((a) => a.position_id)
    .map((a) => ({
      positionId: a.position_id as string,
      accountId: a.account_id,
      accountName: a.account_name,
    }))
}

// A fully-divested holding (services.asset_detail._closed_position_history) — no live
// quantity/market-value/unrealized stats apply (there's nothing left to value), but the real
// lots/realized/income history does, and is worth keeping visible rather than collapsing to a
// bare "No position" empty state (owner report 2026-08-31: "I sold stocks already, but we
// should still show the history"). No banner of our own here — LotsPanel's OWN "No open lots —
// this holding is fully disposed" empty state already says exactly that (owner report
// 2026-08-31: a second banner above it repeated the same sentence). Reuses LotsPanel's
// multi-account `merged` path — the backend already pre-merges every closed position at this
// instrument the same way it merges a multi-account HELD holding, so this is the identical
// rendering, just entered from qty 0.
export function ClosedPositionHistory({ position, symbol, instrumentId, onChanged }: Props) {
  return (
    <LotsPanel
      positionId={position.position_id}
      instrumentId={instrumentId}
      symbol={symbol}
      costCcy={position.cost_currency || 'USD'}
      base={0}
      costBase={null}
      merged={{ lots: position.lots, realized: position.realized, income: position.income }}
      writeTargets={writeTargetsOf(position)}
      onChanged={onChanged}
    />
  )
}
