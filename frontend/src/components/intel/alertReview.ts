import type { PriceAlert } from './WatchlistsSection'

export function isTriggeredNow(alert: PriceAlert, price: number | undefined): boolean {
  if (alert.enabled === false || alert.kind === 'move' || price == null || !Number.isFinite(price))
    return false
  switch (alert.op) {
    case '<=':
      return price <= alert.value
    case '<':
      return price < alert.value
    case '>=':
      return price >= alert.value
    case '>':
      return price > alert.value
  }
}
