// Where one price alert goes when it fires. The alert SAVES its own channel ids (ADR
// 2026-09-30-notification-channels-per-kind-and-per-alert) — nothing here follows another
// setting. Settings' price-alert row is only the default a new alert starts from. The in-app
// Intel story is written for every alert, so it is never a choice here.
import { authFetch } from '../../../api/client'
import { useQueryCache } from '../../../state/queryCache'
import { notifyRoutes, visibleChannels } from '../monitoringConfig'
import type { AlertChannel, MonitoringConfig } from '../monitoringConfig'

export interface AlertRouting {
  channels: AlertChannel[]
  /** Settings' price-alert default: the channels a NEW alert starts with. */
  defaults: string[]
}

export interface Route {
  /** Channels this alert will reach. */
  live: string[]
  /** Channels it is set to reach that are off or removed — skipped. */
  off: string[]
}

export interface NotifyOption {
  value: string
  label: string
  disabled?: boolean
}

const titleCase = (id: string) => id.charAt(0).toUpperCase() + id.slice(1)

export const joinNames = (names: string[]) => names.join(' + ')

/** A channel set's `<select>` value: ids in the connector list's order, so one set has one key. */
export function channelKey(ids: string[], r?: AlertRouting): string {
  const order = r?.channels.map((c) => c.id) ?? []
  const rank = (id: string) => (order.includes(id) ? order.indexOf(id) : order.length)
  return [...new Set(ids)].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b)).join(',')
}

export const keyToChannels = (key: string): string[] => (key ? key.split(',') : [])

export function resolveRoute(ids: string[], r: AlertRouting | undefined): Route {
  const byId = new Map((r?.channels ?? []).map((c) => [c.id, c]))
  const route: Route = { live: [], off: [] }
  for (const id of ids) {
    const ch = byId.get(id)
    if (!r) route.live.push(titleCase(id))
    else (ch?.available ? route.live : route.off).push(ch?.label ?? titleCase(id))
  }
  return route
}

function comboLabel(ids: string[], r: AlertRouting | undefined): string {
  if (!ids.length) return 'In-app only'
  const { live, off } = resolveRoute(ids, r)
  const names = [...live, ...off.map((n) => `${n} (off)`)]
  return `In-app + ${joinNames(names)}`
}

/** Every combination of the channels that are set up (In-app only, + Telegram, + Email,
 * + Telegram + Email, …), plus the alert's saved set when it names a channel that is off —
 * so the form always shows exactly what is saved. */
export function notifyOptions(r: AlertRouting | undefined, saved: string[]): NotifyOption[] {
  const ready = (r?.channels ?? []).filter((c) => c.available).map((c) => c.id)
  const combos: string[][] = [[]]
  for (const id of ready) combos.push(...combos.map((c) => [...c, id]))
  combos.sort((a, b) => a.length - b.length)
  const keys = combos.map((c) => channelKey(c, r))
  const savedKey = channelKey(saved, r)
  if (!keys.includes(savedKey)) keys.push(savedKey)
  const opts: NotifyOption[] = keys.map((k) => ({
    value: k,
    label: comboLabel(keyToChannels(k), r),
  }))
  const missing = visibleChannels(r?.channels ?? [], saved).filter((c) => !c.available)
  if (missing.length) {
    opts.push({
      value: '__setup',
      label: `${joinNames(missing.map((c) => c.label))}: set up in Connectors`,
      disabled: true,
    })
  }
  return opts
}

/** One line for the field's help: what this choice does right now. */
export function routeHelp(ids: string[], r: AlertRouting | undefined): string {
  if (!ids.length) return 'Shows in Intel. Nothing is sent outside the app.'
  const { live, off } = resolveRoute(ids, r)
  const skipped = off.length
    ? ` ${joinNames(off)} ${off.length > 1 ? 'are' : 'is'} off in Connectors, so it's skipped.`
    : ''
  if (!live.length) return `Only shows in Intel for now.${skipped}`
  return `Shows in Intel and on ${joinNames(live)}.${skipped}`
}

/** The alert row's summary of where it goes. */
export function routeSummary(ids: string[], r: AlertRouting | undefined): string {
  if (!ids.length) return 'In-app only'
  const { live } = resolveRoute(ids, r)
  if (!live.length) return 'In-app only · channels off'
  return `In-app + ${joinNames(live)}`
}

async function fetchRouting(): Promise<AlertRouting> {
  const [ch, wl] = await Promise.all([
    authFetch('/api/alert-channels'),
    authFetch('/api/watchlist'),
  ])
  if (!ch.ok || !wl.ok) throw new Error('alert routing unavailable')
  const channels = ((await ch.json()) as { channels?: AlertChannel[] }).channels ?? []
  const cfg = ((await wl.json()) as { config?: MonitoringConfig }).config ?? null
  return { channels, defaults: notifyRoutes(cfg).price_alerts }
}

export function useAlertRouting(): AlertRouting | undefined {
  return useQueryCache<AlertRouting>('intel:alert-routing', fetchRouting, { staleTime: 0 }).data
}
