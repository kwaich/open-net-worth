// QuoteDetailView — the /quote/$symbol route's content. Navigation to this route is
// instant (no pre-navigation network call); this component resolves the ticker to an
// instrument id itself and shows a skeleton/error state while that happens, exactly
// like AssetDetailView already does for its own data fetch.

import { useResolveTicker } from '../../hooks/useResolveTicker'
import { AssetDetailView } from './AssetDetailView'
import { AssetDetailSkeleton } from './AssetDetailSkeleton'
import { ErrorState } from '../ui'

interface Props {
  ticker: string
  seedName?: string
}

export function QuoteDetailView({ ticker, seedName }: Props) {
  const { instrumentId, resolving, error, retry } = useResolveTicker(ticker)

  if (error) {
    return (
      <div className="asd-error">
        <ErrorState
          message={`Could not find a market instrument for "${ticker}".`}
          onRetry={retry}
        />
      </div>
    )
  }

  if (resolving || !instrumentId) {
    return <AssetDetailSkeleton name={seedName} ticker={ticker} />
  }

  return <AssetDetailView instrumentId={instrumentId} seedName={seedName} seedTicker={ticker} />
}
