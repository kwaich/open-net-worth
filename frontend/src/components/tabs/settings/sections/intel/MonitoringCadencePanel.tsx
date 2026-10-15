// Settings › Intelligence → Monitoring: whether Lucius watches at all, how often it checks prices
// and scans news, and the paid/unpaid news sources it uses.
import { fDuration } from '../../../../../utils/format'
import {
  FRESHNESS_OPTS,
  NEWS_OPTS,
  PRICE_OPTS,
  intervalOptions,
} from '../../../../intel/monitoringConfig'
import { Field, Panel, Select, Toggle, row } from '../../ds'
import { NumberInput } from './fields'
import type { PanelProps } from './fields'

export function MonitoringCadencePanel({ cfg, save }: PanelProps) {
  const active = cfg.enabled
  const freshness = cfg.news_freshness ?? 'fast'
  const hotActive = active && freshness !== 'off'
  const webSearch = cfg.web_search_enabled ?? true
  const cadence = active
    ? `Prices every ${fDuration(cfg.price_check_interval_seconds)} · news every ${fDuration(cfg.news_scan_interval_seconds)}`
    : 'Paused — no price checks, scans or alerts'

  return (
    <Panel
      title="Monitoring"
      sub="How often Lucius checks prices and scans news on your watchlists"
    >
      {row(
        'Monitoring active',
        cadence,
        <Toggle
          on={active}
          label="Monitoring active"
          onChange={(v) => save({ enabled: v }, v ? 'Monitoring resumed' : 'Monitoring paused')}
        />,
      )}
      <div className="rd-formgrid">
        <Field label="Price check" help="How often prices are checked.">
          <Select
            aria-label="Price check"
            value={String(cfg.price_check_interval_seconds)}
            disabled={!active}
            onChange={(e) =>
              save({ price_check_interval_seconds: Number(e.target.value) }, 'Price check updated')
            }
          >
            {intervalOptions(PRICE_OPTS, cfg.price_check_interval_seconds).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="News scan interval" help="How often the full news scan runs.">
          <Select
            aria-label="News scan interval"
            value={String(cfg.news_scan_interval_seconds)}
            disabled={!active}
            onChange={(e) =>
              save({ news_scan_interval_seconds: Number(e.target.value) }, 'News scan updated')
            }
          >
            {intervalOptions(NEWS_OPTS, cfg.news_scan_interval_seconds).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="RSS freshness"
          help="Sets the fast-lane cadence (holdings + topics) and keeps the ~90s breaking-news lane running. Off stops both — only the slow ~30 min backfill remains."
        >
          <Select
            aria-label="RSS freshness"
            value={freshness}
            disabled={!active}
            onChange={(e) =>
              save(
                { news_freshness: e.target.value as 'off' | 'balanced' | 'fast' },
                'RSS freshness updated',
              )
            }
          >
            {FRESHNESS_OPTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      {row(
        `Breaking news lane · ${hotActive ? 'Active' : 'Off'} · ~90s`,
        hotActive
          ? 'Checked while on: FT, CNBC, MarketWatch, WSJ and TechCrunch headlines.'
          : 'Turn RSS freshness on to enable outlet headlines.',
        <Toggle
          on={cfg.outlet_feeds_enabled ?? true}
          disabled={!hotActive}
          label="Outlet headlines"
          onChange={(v) =>
            save({ outlet_feeds_enabled: v }, v ? 'Outlet headlines on' : 'Outlet headlines off')
          }
        />,
      )}
      {row(
        'Web search',
        'Uses the search budget below; RSS always runs regardless of this setting.',
        <Toggle
          on={webSearch}
          disabled={!active}
          label="Web search"
          onChange={(v) => save({ web_search_enabled: v }, v ? 'Web search on' : 'Web search off')}
        />,
      )}
      <div className="rd-formgrid">
        <Field
          label="Web search budget (calls / scan)"
          help="Max paid searches per scan; RSS is always free. Only applies when Web search is on."
        >
          <NumberInput
            id="search-budget"
            value={cfg.search_budget ?? 15}
            min={1}
            max={50}
            disabled={!webSearch || !active}
            onCommit={(n) => save({ search_budget: n }, 'Search budget updated')}
          />
        </Field>
      </div>
    </Panel>
  )
}
