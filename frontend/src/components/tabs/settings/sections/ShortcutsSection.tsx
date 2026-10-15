// Settings → Phone shortcuts: log money movement from the phone as it happens.
//
// iPhone: signed, generic shortcuts + a setup code (address + API token). On a computer the QR
// (opening /phone-setup on the phone) shows straight away; on the iPhone itself the install rows
// are the tab — no QR, no sub-page, nothing to navigate back from. Android gets the capture
// request to forward to. The
// setup code and token stay masked until revealed, inside data-ctx-secret regions, so the Lucius
// screen snapshot never reads them.
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { authFetch } from '../../../../api/client'
import { useCachedFetchState } from '../../../../state/queryCache'
import { docIdToPath } from '../../../help/docId'
import { Bell, Wallet } from '../../../../ui/icons'
import { openShortcutInstall } from '../../../../utils/openShortcutInstall'
import { Badge, Btn, Field, Panel, SegTabs, useSectionBusy } from '../ds'
import type { SectionProps } from '../ds'
import { addressWarning, iosMajor, isOwnComputer, phonePlatform } from './phoneShortcuts'

interface ShortcutRow {
  slug: string
  signed: boolean
}

interface ShortcutCatalog {
  base_url: string
  capture_path: string
  shortcuts: ShortcutRow[]
}

interface Prepared {
  setup_url: string
  qr_data_uri: string
  expires_in: number
  base_url: string
}

interface SetupShortcut {
  slug: string
  name: string
  install_url: string | null
}

interface SetupData {
  setup_code: string
  shortcuts: SetupShortcut[]
}

type Toast = SectionProps['toast']

const DOC = docIdToPath('features/phone-shortcuts')
const MASK = '••••••••••••••••••••'
const CODE_NOTE = 'Your address and API token. Each shortcut asks for it once.'
// Re-mint this long before a QR's ticket expires, so the one on screen always scans.
const QR_REFRESH_MARGIN_S = 30
// The iPhone tab is split by iOS version: iOS 27 stores the trigger inside the shortcut, iOS 26
// and earlier run it from a separate Wallet automation — different files, QR and steps.
const TABS = [
  { value: 'ios27', label: 'iOS 27+' },
  { value: 'ios26', label: 'iOS 26 or earlier' },
  { value: 'android', label: 'Android' },
] as const
type Tab = (typeof TABS)[number]['value']
type Ios = '27' | '26'
const APPLE_PAY: Record<Ios, string> = { '27': 'log-apple-pay', '26': 'log-apple-pay-ios26' }

function defaultTab(): Tab {
  if (phonePlatform() === 'android') return 'android'
  const major = iosMajor()
  return major !== null && major <= 26 ? 'ios26' : 'ios27'
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await authFetch(path, init)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<T>
}

function prepare(base: string, ios: Ios = '27'): Promise<Prepared> {
  return getJson<Prepared>('/api/shortcuts/prepare', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ base_url: base || undefined, ios }),
  })
}

function copy(text: string, what: string, toast: Toast) {
  void navigator.clipboard
    .writeText(text)
    .then(() => toast(`Copied ${what}`))
    .catch(() => toast(`Could not copy ${what}`))
}

export function ShortcutsSection({ toast, openSection }: SectionProps) {
  const platform = phonePlatform()
  const { data, initialLoading } = useCachedFetchState<ShortcutCatalog>(
    '/api/shortcuts',
    () => getJson<ShortcutCatalog>('/api/shortcuts'),
    { staleTime: 0 },
  )
  useSectionBusy(initialLoading && !data)
  const [baseUrl, setBaseUrl] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>(defaultTab)
  const typed = baseUrl ?? data?.base_url ?? ''
  const base = typed.trim().replace(/\/+$/, '')
  const ios: Ios = tab === 'ios26' ? '26' : '27'
  const unsigned = !!data?.shortcuts.some((s) => s.slug === APPLE_PAY[ios] && !s.signed)

  const address = (
    <AddressField
      value={typed}
      warning={base ? addressWarning(base) : null}
      onChange={setBaseUrl}
    />
  )
  // Grouped cards, like every other Settings section: what you copy, what you install, then
  // the two links. One panel per job — never one card holding the whole flow.
  return (
    <>
      <div className="rd-phone-tabs">
        <SegTabs ariaLabel="Phone" options={TABS} value={tab} onChange={setTab} />
      </div>
      {tab !== 'android' ? (
        platform === 'ios' ? (
          <ThisIphoneSetup key={ios} base={base} ios={ios} unsigned={unsigned} toast={toast} />
        ) : (
          <IphoneSetup base={base} ios={ios} address={address} unsigned={unsigned} toast={toast} />
        )
      ) : (
        <AndroidSetup
          base={base}
          address={address}
          capturePath={data?.capture_path ?? '/api/capture'}
          toast={toast}
        />
      )}
      <div className="rd-actions">
        <a className="rd-btn ghost xs" href={DOC}>
          Setup guide
        </a>
        <Btn size="xs" variant="ghost" onClick={() => openSection?.('security')}>
          Manage API token
        </Btn>
      </div>
    </>
  )
}

function AddressField({
  value,
  warning,
  onChange,
}: {
  value: string
  warning: string | null
  onChange: (value: string) => void
}) {
  return (
    <Field
      span="full"
      label="App address"
      help={
        warning ? (
          <span className="rd-help--warn">{warning}</span>
        ) : (
          'Where your phone reaches this app'
        )
      }
    >
      <input
        className="rd-input rd-mono"
        spellCheck={false}
        aria-label="App address"
        aria-invalid={warning ? true : undefined}
        placeholder="https://your-address"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  )
}

function IphoneSetup({
  base,
  ios,
  address,
  unsigned,
  toast,
}: {
  base: string
  ios: Ios
  address: ReactNode
  unsigned: boolean
  toast: Toast
}) {
  return (
    <>
      <Panel title="Scan with your iPhone">
        <div className="rd-phone">
          <SetupQr base={base} ios={ios} />
          <ol className="rd-steps">
            <li>Open the link on your iPhone</li>
            <li>{ios === '27' ? 'Install the shortcuts you want' : 'Install the shortcut'}</li>
            <li>Follow their steps below</li>
          </ol>
        </div>
        {unsigned ? <UnsignedNote /> : null}
      </Panel>
      <ShortcutsPanel ios={ios} unsigned={unsigned} />
      <Panel title="Connection">
        <div className="rd-formgrid">
          {address}
          <Field span="full" label="Setup code" help={CODE_NOTE}>
            <SetupCodeField key={base} prefix={base} load={() => loadCode(base)} toast={toast} />
          </Field>
        </div>
      </Panel>
    </>
  )
}

/** On the iPhone itself: the three steps right here — no QR (this IS the phone), no address
 *  (it is the one this page was opened on), no sub-page (the sheet's own back is the only one). */
function ThisIphoneSetup({
  base,
  ios,
  unsigned,
  toast,
}: {
  base: string
  ios: Ios
  unsigned: boolean
  toast: Toast
}) {
  // The install links, fetched as soon as the steps show and re-fetched before they expire:
  // a tap must open a link it already has. iOS refuses a window opened after an await, and a
  // blank sheet opened first then pointed at the file stays about:blank (seen on device,
  // 2026-10-13) — the home-screen app cannot steer the sheet it opened.
  const [links, setLinks] = useState<Record<string, SetupShortcut>>({})
  useEffect(() => {
    let live = true
    let refresh: ReturnType<typeof setTimeout> | undefined
    const load = async () => {
      try {
        const ticket = await prepare(base, ios)
        const t = new URL(ticket.setup_url).searchParams.get('t') ?? ''
        const setup = await getJson<SetupData>(`/api/shortcuts/setup?t=${encodeURIComponent(t)}`)
        if (!live) return
        const next: Record<string, SetupShortcut> = {}
        for (const sc of setup.shortcuts) if (sc.install_url) next[sc.slug] = sc
        setLinks(next)
        const wait = Math.max(QR_REFRESH_MARGIN_S, ticket.expires_in - QR_REFRESH_MARGIN_S)
        refresh = setTimeout(() => void load(), wait * 1000)
      } catch {
        if (live) setLinks({})
      }
    }
    // Coming back to the app (from Safari, Shortcuts, the home screen) re-fetches: a ticket
    // lives in server memory, so a restart while the page sat open killed every link on it
    // (seen on device, 2026-10-13 — Install got a 404).
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      clearTimeout(refresh)
      void load()
    }
    void load()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      live = false
      clearTimeout(refresh)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [base, ios])

  function install(slug: string) {
    const sc = links[slug]
    if (sc?.install_url) openShortcutInstall(sc.install_url)
    else toast('Still preparing the install link — try again in a moment.')
  }

  return (
    <>
      <Panel title="Setup code" sub={CODE_NOTE} className="rd-panel--roomy">
        <SetupCodeField key={base} prefix={base} load={() => loadCode(base)} toast={toast} />
      </Panel>
      <ShortcutsPanel ios={ios} unsigned={unsigned} onInstall={install} />
    </>
  )
}

/** The shortcuts for this iOS, each with its steps. On the iPhone each carries Install; on a
 *  computer it's the same list without it — installing happens on the phone, via the QR. */
function ShortcutsPanel({
  ios,
  unsigned,
  onInstall,
}: {
  ios: Ios
  unsigned: boolean
  onInstall?: (slug: string) => void
}) {
  const paste = 'Open the download, install the shortcut, paste your setup code'
  return (
    <Panel
      title="Shortcuts"
      sub={ios === '27' ? 'Install either or both.' : undefined}
      className="rd-panel--roomy"
      flush
    >
      <ShortcutOption
        icon={<Wallet size={16} strokeWidth={1.7} aria-hidden="true" />}
        title="Apple Pay taps"
        sub="Amount, merchant and card, as you tap"
        unsigned={unsigned}
        onInstall={onInstall && (() => onInstall(APPLE_PAY[ios]))}
        steps={[
          paste,
          ios === '27' ? (
            <>
              <b>Search → Automation → Wallet</b> → your cards
            </>
          ) : (
            <>
              <b>Automation → + → Wallet</b> → your cards → <b>Run Immediately</b>
            </>
          ),
        ]}
      />
      {ios === '27' ? (
        <ShortcutOption
          icon={<Bell size={16} strokeWidth={1.7} aria-hidden="true" />}
          title="Finance notifications"
          sub="Bank, wallet, broker and crypto alerts"
          unsigned={false}
          onInstall={onInstall && (() => onInstall('log-notifications'))}
          steps={[
            paste,
            <>
              <b>Search → Automation → Notification → App</b> → your apps
            </>,
          ]}
        />
      ) : null}
    </Panel>
  )
}

/** One way to log, as a settings row — what it is, Install (on the phone), then its two steps. */
function ShortcutOption({
  icon,
  title,
  sub,
  unsigned,
  onInstall,
  steps,
}: {
  icon: ReactNode
  title: string
  sub: string
  unsigned: boolean
  onInstall?: () => void
  steps: ReactNode[]
}) {
  return (
    <div className="rd-row rd-row--option">
      <span className="rd-row-ic">{icon}</span>
      <div className="rd-row-main">
        <div className="rd-row-title">{title}</div>
        <div className="rd-row-sub">{sub}</div>
      </div>
      {unsigned ? (
        <Badge tone="warn">Not signed</Badge>
      ) : onInstall ? (
        <Btn size="xs" variant="primary" onClick={onInstall}>
          Install
        </Btn>
      ) : null}
      <ol className="rd-steps">
        {steps.map((step, n) => (
          <li key={n}>{step}</li>
        ))}
      </ol>
    </div>
  )
}

function UnsignedNote() {
  return (
    <span className="rd-help">
      <Badge tone="warn">Not signed</Badge> iOS will refuse this build’s shortcut —{' '}
      <a href={DOC}>build it by hand</a>.
    </span>
  )
}

async function loadCode(base: string): Promise<string> {
  const q = base ? `?base_url=${encodeURIComponent(base)}` : ''
  return (await getJson<{ setup_code: string }>(`/api/shortcuts/setup-code${q}`)).setup_code
}

/** The QR a phone scans, shown as soon as the section opens and re-minted before it expires
 *  or when the address changes. Never for this computer's own address — that QR can't work. */
function SetupQr({ base, ios }: { base: string; ios: Ios }) {
  const [prepared, setPrepared] = useState<Prepared | null>(null)
  const [failed, setFailed] = useState(false)
  const usable = !!base && !isOwnComputer(base)

  useEffect(() => {
    if (!usable) return
    let live = true
    let refresh: ReturnType<typeof setTimeout> | undefined
    const mint = () =>
      prepare(base, ios)
        .then((ticket) => {
          if (!live) return
          setPrepared(ticket)
          setFailed(false)
          const wait = Math.max(QR_REFRESH_MARGIN_S, ticket.expires_in - QR_REFRESH_MARGIN_S)
          refresh = setTimeout(mint, wait * 1000)
        })
        .catch(() => live && setFailed(true))
    // Debounced: typing an address must not mint a ticket per keystroke.
    const start = setTimeout(mint, 400)
    return () => {
      live = false
      clearTimeout(start)
      clearTimeout(refresh)
    }
  }, [base, ios, usable])

  const ready =
    usable &&
    !failed &&
    prepared &&
    prepared.base_url === base &&
    prepared.setup_url.endsWith(`ios=${ios}`)
  const loading = usable && !failed && !ready
  return (
    <div className={'rd-qr-slot' + (loading ? ' loading' : '')} data-ctx-secret="">
      {ready ? (
        <img
          // Keyed by the code so a NEW code fades in; the same code re-rendering doesn't flash.
          key={prepared.qr_data_uri}
          className="rd-qr"
          src={prepared.qr_data_uri}
          alt="QR code that opens phone setup"
        />
      ) : loading ? (
        <span className="rd-qr-loading" role="status" aria-label="Generating QR code" />
      ) : (
        <span className="rd-qr-empty">
          {failed ? 'Couldn’t make a QR' : 'Add your app address'}
        </span>
      )}
    </div>
  )
}

/** The setup code the way Security shows the API token: masked until Reveal, Copy beside it,
 *  inside a data-ctx-secret region so the Lucius screen snapshot never reads it. */
function SetupCodeField({
  prefix,
  load,
  toast,
}: {
  prefix: string
  load: () => Promise<string>
  toast: Toast
}) {
  const [code, setCode] = useState<string | null>(null)
  const [reveal, setReveal] = useState(false)
  const [copied, setCopied] = useState(false)

  // Loaded up front (kept off the page until Reveal): iOS lets a page write the clipboard only
  // inside the tap itself, so a Copy that first waited on this request copied nothing — no
  // toast, no clipboard (seen on device, 2026-10-13).
  useEffect(() => {
    let live = true
    load()
      .then((loaded) => live && setCode(loaded))
      .catch(() => undefined)
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one load per field (keyed by base)
  }, [])

  useEffect(() => {
    if (!copied) return
    const t = setTimeout(() => setCopied(false), 1500)
    return () => clearTimeout(t)
  }, [copied])

  function copyCode(text: string) {
    void navigator.clipboard
      .writeText(text)
      .then(() => setCopied(true))
      .catch(() => toast('Could not copy the setup code'))
  }

  async function get(): Promise<string | null> {
    if (code) return code
    try {
      const loaded = await load()
      setCode(loaded)
      return loaded
    } catch {
      toast('Could not read the setup code')
      return null
    }
  }

  return (
    <>
      <div className="rd-secret-row" data-ctx-secret="">
        {/* Not an input: a read-only field still takes focus and shows a caret on iOS. The
            address shrinks with an ellipsis so the mask always shows it's a secret. */}
        <div
          className={'rd-input rd-mono rd-secret-value' + (reveal && code ? ' revealed' : '')}
          role="textbox"
          aria-readonly="true"
          aria-label="Setup code"
        >
          {reveal && code ? (
            code
          ) : (
            <>
              <span className="rd-secret-addr">{prefix || '…'}</span>
              <span className="rd-secret-mask">|{MASK.slice(0, 8)}</span>
            </>
          )}
        </div>
        <Btn
          size="xs"
          onClick={() => {
            if (reveal) setReveal(false)
            else void get().then((c) => c && setReveal(true))
          }}
        >
          {reveal ? 'Hide' : 'Reveal'}
        </Btn>
        <Btn
          size="xs"
          onClick={() => {
            if (code) copyCode(code)
            else void get().then((c) => c && copyCode(c))
          }}
        >
          {copied ? 'Copied' : 'Copy'}
        </Btn>
      </div>
    </>
  )
}

function AndroidSetup({
  base,
  address,
  capturePath,
  toast,
}: {
  base: string
  address: ReactNode
  capturePath: string
  toast: Toast
}) {
  const [token, setToken] = useState<string | null>(null)
  const [reveal, setReveal] = useState(false)
  const url = `${base}${capturePath}`
  const request = (bearer: string) => `POST ${url}
Authorization: Bearer ${bearer}
Content-Type: application/json

{"text": "<the notification's text>", "source": "android"}`

  async function getToken(): Promise<string | null> {
    if (token) return token
    try {
      const loaded = (await getJson<{ token: string }>('/api/auth/api-token')).token
      setToken(loaded)
      return loaded
    } catch {
      toast('Could not read the API token')
      return null
    }
  }

  return (
    <>
      <Panel title="Connection">
        <div className="rd-formgrid">{address}</div>
      </Panel>
      <Panel
        className="rd-panel--roomy"
        title="Request"
        sub="Forward finance notifications here with Tasker or MacroDroid."
      >
        <div className="rd-block">
          {/* data-ctx-secret: a revealed token here must never reach the Lucius screen snapshot. */}
          <div className="rd-code" data-ctx-secret="">
            {request(reveal && token ? token : MASK)}
          </div>
          <div className="rd-actions">
            <Btn
              size="xs"
              variant="primary"
              onClick={() =>
                void getToken().then(
                  (t) => t && copy(request(t), 'the request with your API token', toast),
                )
              }
            >
              Copy request
            </Btn>
            <Btn
              size="xs"
              onClick={() => {
                if (reveal) setReveal(false)
                else void getToken().then((t) => t && setReveal(true))
              }}
            >
              {reveal ? 'Hide token' : 'Reveal token'}
            </Btn>
          </div>
        </div>
        <span className="rd-help">
          Add <code>&quot;ref&quot;: &quot;%TIMEMS&quot;</code> so a retry never logs twice.
        </span>
      </Panel>
    </>
  )
}
