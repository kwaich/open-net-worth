// AssetLink — renders a tappable element that opens the asset-detail page.
//
// Pass `instrumentId` when you have it, or `ticker` (and optionally `label`) when
// only the symbol is known — either way navigation is immediate (no network call
// first): a ticker goes to /quote/$symbol, which resolves itself once mounted.
//
// When neither `instrumentId` nor `ticker` is available, the children are
// wrapped in a plain <span> — not interactive, no extra affordance.
//
// A11y: uses a real <button> so it is keyboard-focusable (Enter/Space) and
// announced correctly. Never nests a button inside an existing interactive
// ancestor — callers must ensure the surrounding DOM is non-interactive when
// embedding this.

import type { ReactNode } from 'react'
import { useAssetNav } from '../../state/assetNav'

interface Props {
  instrumentId?: string
  ticker?: string
  label?: string
  currency?: string
  className?: string
  children: ReactNode
}

export function AssetLink({ instrumentId, ticker, label, className = '', children }: Props) {
  const { openAsset } = useAssetNav()
  const isNavigable = !!(instrumentId || ticker)

  if (!isNavigable) {
    return <span className={className || undefined}>{children}</span>
  }

  const title = `Open ${ticker || label || instrumentId}`

  return (
    <button
      type="button"
      className={`asset-link asset-link--clickable${className ? ` ${className}` : ''}`}
      title={title}
      onClick={() => openAsset({ instrumentId, ticker, label })}
    >
      {children}
    </button>
  )
}
