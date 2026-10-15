// Settings › Intelligence → Alerts: where each KIND of Intel update reaches you (one grid,
// ADR 2026-09-30-notification-channels-per-kind-and-per-alert). The scheduled daily digest was
// removed — a Daily briefing Automation does that job.
import type { AlertChannel, NotifyKind, NotifyRoutes } from '../../../../intel/monitoringConfig'
import { NOTIFY_KINDS, notifyRoutes, visibleChannels } from '../../../../intel/monitoringConfig'
import { InfoPopover } from '../../../../ui/InfoPopover'
import { Panel } from '../../ds'
import type { PanelProps } from './fields'

function RoutesGrid({
  routes,
  channels,
  onChange,
}: {
  routes: NotifyRoutes
  channels: AlertChannel[]
  onChange: (kind: NotifyKind, next: string[]) => void
}) {
  return (
    <div className="rd-nroutes-wrap">
      <table className="rd-nroutes">
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">Kind of update</span>
            </th>
            <th scope="col">In-app</th>
            {channels.map((ch) => (
              <th scope="col" key={ch.id}>
                <span className="rd-nroutes-ch">
                  {ch.label}
                  {!ch.available && (
                    <InfoPopover
                      width={220}
                      text={
                        Object.values(routes).some((ids) => ids.includes(ch.id))
                          ? `${ch.label} is turned off in Connectors, so it's skipped until you turn it back on.`
                          : `${ch.label} isn't set up yet. Connect it in Connectors to send updates there.`
                      }
                    />
                  )}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {NOTIFY_KINDS.map((k) => (
            <tr key={k.id}>
              <th scope="row">
                <span className="rd-nroutes-kind">{k.label}</span>
                <span className="rd-nroutes-sub">{k.sub}</span>
              </th>
              <td>
                <input type="checkbox" checked disabled aria-label={`${k.label} in-app`} />
              </td>
              {channels.map((ch) => {
                const on = routes[k.id].includes(ch.id)
                // Chosen, then its connector was turned off: skipped until it's back. Still
                // clickable so it can be cleared here.
                const stale = on && !ch.available
                return (
                  <td key={ch.id} className={stale ? 'rd-nroutes-stale' : undefined}>
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={!ch.available && !on}
                      title={stale ? `${ch.label} is off in Connectors — skipped` : undefined}
                      aria-label={`${k.label} via ${ch.label}`}
                      onChange={(e) =>
                        onChange(
                          k.id,
                          e.target.checked
                            ? [...routes[k.id], ch.id]
                            : routes[k.id].filter((c) => c !== ch.id),
                        )
                      }
                    />
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function AlertsPanel({ cfg, save, channels }: PanelProps & { channels: AlertChannel[] }) {
  const routes = notifyRoutes(cfg)

  const setRoute = (kind: NotifyKind, next: string[]) => {
    const label = NOTIFY_KINDS.find((k) => k.id === kind)?.label ?? 'Notifications'
    save({ notify_routes: { ...routes, [kind]: next } }, `${label} updated`)
  }

  return (
    <Panel
      title="Alerts"
      sub="Where each kind of update reaches you. Everything always shows in the app too."
    >
      <RoutesGrid
        routes={routes}
        channels={visibleChannels(channels, Object.values(routes).flat())}
        onChange={setRoute}
      />
    </Panel>
  )
}
