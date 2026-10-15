// Settings → Appearance. Ported from the AppearanceSection in
// design_handoffs/settings_handoff/prototype/redesign/settings.jsx.
//
// No Save button: text commits on blur (or Enter), swatches on click. The subtitle is doing
// real work — this is INSTANCE config, not part of your profile, so it applies to everyone
// using the instance.
import { useEffect, useState } from 'react'
import { authFetch } from '../../../../api/client'
import {
  getTickerEnabled,
  getTickerMarketAlways,
  setTickerEnabled,
  setTickerMarketAlways,
} from '../../../../tickerPref'
import { setCachedQueryData, useCachedFetchState } from '../../../../state/queryCache'
import { Field, Panel, Select, Toggle, useSectionBusy } from '../ds'
import type { SectionProps } from '../ds'
import { THEME_OPTIONS, useTheme, type ThemePreference } from '../../../../theme'

interface AppConfig {
  app_name?: string
  accent_color?: string
  logo_url?: string
}

const SWATCHES = ['#3b86dd', '#34b08a', '#9a7fd0', '#e0a032']

/** Apply immediately so the operator sees the change without a reload — the ThemeProvider
 *  reads /api/config only at boot. */
function applyLive(c: AppConfig) {
  if (c.accent_color) document.documentElement.style.setProperty('--accent', c.accent_color)
  else document.documentElement.style.removeProperty('--accent')
  if (c.app_name) document.title = c.app_name
}

export function AppearanceSection({ toast }: SectionProps) {
  const { preference, setPreference } = useTheme()
  const [tickerOn, setTickerOn] = useState(getTickerEnabled)
  const [marketAlways, setMarketAlways] = useState(getTickerMarketAlways)
  const { data, initialLoading, refresh } = useCachedFetchState<AppConfig>(
    '/api/config',
    async () => {
      const r = await authFetch('/api/config')
      if (!r.ok) throw new Error(String(r.status))
      return r.json()
    },
    { staleTime: 0 },
  )
  useSectionBusy(initialLoading && !data)
  const [accent, setAccent] = useState(data?.accent_color || '')

  useEffect(() => {
    if (data) setAccent(data.accent_color || '')
  }, [data])

  async function save(patch: AppConfig, message: string) {
    const previousAccent = data?.accent_color || ''
    try {
      const res = await authFetch('/api/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        // ONLY the changed field. Re-asserting the others from a possibly-stale closure
        // means typing an app name and then clicking a swatch reverts the name.
        body: JSON.stringify(patch),
      })
      if (!res.ok) {
        const body = (await res.json().catch(() => null)) as { detail?: string } | null
        toast(body?.detail || `Could not save (HTTP ${res.status})`)
        // The swatch was selected optimistically; put it back rather than showing a
        // selection the server never accepted.
        if (patch.accent_color !== undefined) setAccent(previousAccent)
        void refresh()
        return
      }
      const c = (await res.json()) as AppConfig
      setCachedQueryData('/api/config', c)
      applyLive(c)
      toast(message)
    } catch {
      toast('Could not reach the server. Try again.')
      if (patch.accent_color !== undefined) setAccent(previousAccent)
      void refresh()
    }
  }

  // Committing per keystroke would toast per keystroke; defaultValue is re-baselined on
  // commit so re-blurring an unchanged field is silent.
  const commit =
    (key: 'app_name' | 'logo_url', what: string) => (e: React.FocusEvent<HTMLInputElement>) => {
      const next = e.target.value
      if (next === e.target.defaultValue) return
      e.target.defaultValue = next
      void save({ [key]: next.trim() }, what + ' updated')
    }

  return (
    <>
      {/* Theme was DROPPED by the Settings port — it lived in AppearanceSettings.tsx, which the
          new sections replaced but never re-implemented, and nothing renders that file now.
          First, because it is what people open this section for; and unlike the brand fields
          below it is a per-DEVICE preference (localStorage, instant, no Save). */}
      <Panel title="Theme" sub="Applies to this device only — instantly, with no Save">
        <div className="rd-formgrid">
          <Field label="Color scheme">
            <Select
              value={preference}
              onChange={(e) => setPreference(e.target.value as ThemePreference)}
              aria-label="Color scheme"
            >
              {THEME_OPTIONS.map(({ value, label }) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="Status bar price ticker"
            help="Scroll live prices for your holdings, commodities, coins and FX across the status bar"
          >
            <Toggle
              on={tickerOn}
              onChange={(v) => {
                setTickerEnabled(v)
                setTickerOn(v)
              }}
              label="Status bar price ticker"
            />
          </Field>
          <Field
            label="Always show market prices"
            help="Gold, coins, FX and a few large stocks lead the ticker only once you hold market positions or follow a watchlist. Turn this on to show them regardless."
          >
            <Toggle
              on={marketAlways}
              onChange={(v) => {
                setTickerMarketAlways(v)
                setMarketAlways(v)
              }}
              label="Always show market prices"
            />
          </Field>
        </div>
      </Panel>
      <Panel
        title="Appearance"
        sub="Brand this instance — its name, accent color, and an optional logo. Applies to everyone using this instance; it is instance config, not part of your profile."
      >
        <div className="rd-formgrid">
          <Field label="App name" help="Shown in the sidebar and browser tab">
            <input
              className="rd-input"
              key={'name-' + (data?.app_name ?? '')}
              defaultValue={data?.app_name ?? ''}
              onBlur={commit('app_name', 'App name')}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
              }}
            />
          </Field>
          <Field label="Logo URL" help="Optional — shown in the sidebar">
            <input
              className="rd-input"
              placeholder="https://…/logo.svg"
              key={'logo-' + (data?.logo_url ?? '')}
              defaultValue={data?.logo_url ?? ''}
              onBlur={commit('logo_url', 'Logo')}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
              }}
            />
          </Field>
          <Field label="Accent color" help="Empty resets to the built-in accent">
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              {SWATCHES.map((c) => (
                <button
                  type="button"
                  key={c}
                  onClick={() => {
                    setAccent(c)
                    void save({ accent_color: c }, 'Accent updated')
                  }}
                  aria-label={'Accent ' + c}
                  aria-pressed={accent === c}
                  style={{
                    width: 26,
                    height: 26,
                    padding: 0,
                    flex: 'none',
                    boxSizing: 'border-box',
                    borderRadius: '50%',
                    background: c,
                    cursor: 'pointer',
                    border: 'none',
                    boxShadow:
                      accent === c ? '0 0 0 2px var(--surface), 0 0 0 4px var(--ink)' : 'none',
                  }}
                />
              ))}
            </div>
          </Field>
        </div>
      </Panel>
    </>
  )
}
