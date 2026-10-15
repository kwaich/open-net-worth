import { Panel, Skeleton } from '../ui'
import { fCurrency, plClass } from '../../utils/format'
import type { PeerItem } from '../../types/assetDetail'
import { AssetLink } from './AssetLink'

interface Props {
  peers: PeerItem[]
  /** True while a real facts/peers synthesis pass is actually running (`refreshing` from
   *  useAssetDetail — see KeyFactsCard's matching prop). Owner-reproduced live bug,
   *  2026-08-31 (KODK): with no peers yet and no loading state, this card just silently
   *  rendered nothing — no skeleton, no indication anything was happening. */
  generating?: boolean
}

export function PeersCard({ peers, generating }: Props) {
  if (!peers.length) {
    // A completed pass can legitimately land zero peers (only comparables actually
    // mentioned in real news coverage are ever shown — see asset_brief.py's own peers
    // prompt) — that's a real, final answer, not a loading state, so this only shows
    // while `generating` is true.
    if (!generating) return null
    return (
      <Panel
        title="Peers"
        className="asd-rail-panel"
        actions={
          <span className="asd-pi-updating" role="status" aria-label="Peers are generating">
            <span className="ui-btn__spinner" aria-hidden="true" />
            Generating
          </span>
        }
      >
        <div className="asd-skel-stack" role="status" aria-label="Generating">
          <Skeleton height={13} width="80%" />
          <Skeleton height={13} width="65%" />
        </div>
      </Panel>
    )
  }

  return (
    <Panel title="Peers" className="asd-rail-panel">
      <div className="asd-peers-list">
        {peers.map((peer, i) => (
          <AssetLink
            key={i}
            ticker={peer.ticker ?? undefined}
            label={peer.name}
            className="asd-peer-row"
          >
            <div className="asd-peer-info">
              <span className="asd-peer-name">{peer.name}</span>
              {(peer.ticker || peer.exchange) && (
                <span className="asd-peer-sub">
                  {[peer.ticker, peer.exchange].filter(Boolean).join(' • ')}
                </span>
              )}
            </div>
            {peer.price != null && (
              <div className="asd-peer-price">
                <span className="asd-peer-price-val">
                  {peer.currency
                    ? fCurrency(peer.price, peer.currency, {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })
                    : peer.price.toLocaleString(undefined, {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })}
                </span>
                {peer.change_pct != null && (
                  <span className={`asd-peer-change ${plClass(peer.change_pct)}`}>
                    {peer.change_pct >= 0 ? '+' : ''}
                    {peer.change_pct.toFixed(2)}%
                  </span>
                )}
              </div>
            )}
          </AssetLink>
        ))}
      </div>
    </Panel>
  )
}
