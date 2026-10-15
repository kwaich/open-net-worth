// /phone-setup?t=… — the page a phone opens from the QR in Settings → Phone shortcuts.
//
// Rendered BEFORE the sign-in gate (main.tsx): the phone has no session, the ticket in the URL
// is its credential, and GET /api/shortcuts/setup is what it unlocks (bound to this device on
// first open, dead after 10 minutes or a token rotation). There is no Lucius on this page, so
// showing the setup code as text is fine here — it never is inside the app.
import { useEffect, useState } from 'react'
import { useBrand } from '../../brand-context'
import { openShortcutInstall } from '../../utils/openShortcutInstall'
import {
  Badge,
  Button,
  Container,
  ErrorState,
  List,
  Loading,
  PageHeader,
  Panel,
  SegmentedControl,
  Stack,
  Text,
  TextArea,
} from '../ui'
import { iosMajor } from '../tabs/settings/sections/phoneShortcuts'
import { loadSetup, type SetupData, type SetupShortcut } from './setupData'

type Load =
  | { state: 'loading' }
  | { state: 'gone'; message: string }
  | { state: 'ready'; data: SetupData }

const GONE =
  'This setup link expired, or was already opened on another device. Make a new one in Settings → Phone shortcuts.'

export function PhoneSetupPage() {
  const brand = useBrand()
  const [load, setLoad] = useState<Load>({ state: 'loading' })

  useEffect(() => {
    // The page URL carries the ticket — never pass it on as a Referer.
    const meta = document.createElement('meta')
    meta.name = 'referrer'
    meta.content = 'no-referrer'
    document.head.appendChild(meta)
    let live = true
    const t = new URLSearchParams(window.location.search).get('t') ?? ''
    loadSetup(t)
      .then((data) => live && setLoad({ state: 'ready', data }))
      .catch(() => live && setLoad({ state: 'gone', message: GONE }))
    return () => {
      live = false
      meta.remove()
    }
  }, [])

  return (
    <main className="phone-setup">
      <Container size="narrow">
        <Stack gap="--space-6">
          <Text size="sm" weight={700} tone="faint">
            {brand.app_name.toUpperCase()}
          </Text>
          <PageHeader
            title="Phone shortcuts"
            subtitle={
              load.state === 'ready'
                ? `This link works on this phone for ${Math.round(load.data.expires_in / 60)} minutes.`
                : undefined
            }
          />
          {load.state === 'loading' ? <Loading label="Opening setup…" /> : null}
          {load.state === 'gone' ? (
            <ErrorState title="Link no longer works" message={load.message} />
          ) : null}
          {load.state === 'ready' ? <Steps data={load.data} /> : null}
        </Stack>
      </Container>
    </main>
  )
}

type Ios = '27' | '26'
const IOS_OPTIONS = [
  { value: '27' as const, label: 'iOS 27+' },
  { value: '26' as const, label: 'iOS 26 or earlier' },
]

/** The version the QR was made for (``?ios=``), else this phone's own, else iOS 27. */
function initialIos(): Ios {
  const fromLink = new URLSearchParams(window.location.search).get('ios')
  if (fromLink === '27' || fromLink === '26') return fromLink
  const major = iosMajor()
  return major !== null && major <= 26 ? '26' : '27'
}

function Steps({ data }: { data: SetupData }) {
  const [ios, setIos] = useState<Ios>(initialIos)
  const applePay = data.shortcuts.find(
    (s) => s.slug === (ios === '27' ? 'log-apple-pay' : 'log-apple-pay-ios26'),
  )
  const notifications = data.shortcuts.find((s) => s.slug === 'log-notifications')
  return (
    <>
      <SegmentedControl
        ariaLabel="iOS version"
        options={IOS_OPTIONS}
        value={ios}
        onChange={setIos}
      />
      <Panel
        title="Setup code"
        subtitle="Your address and API token. Each shortcut asks for it once."
      >
        <CopyCode code={data.setup_code} />
      </Panel>
      {applePay ? (
        <OptionPanel
          title="Apple Pay taps"
          what="Amount, merchant and card, as you tap"
          shortcut={applePay}
          trigger={
            ios === '27' ? (
              <>
                <strong>Search → Automation → Wallet</strong> → your cards
              </>
            ) : (
              <>
                <strong>Automation → + → Wallet</strong> → your cards →{' '}
                <strong>Run Immediately</strong>
              </>
            )
          }
        />
      ) : null}
      {ios === '27' && notifications ? (
        <>
          <OptionPanel
            title="Finance notifications"
            what="Bank, wallet, broker and crypto alerts"
            shortcut={notifications}
            trigger={
              <>
                <strong>Search → Automation → Notification → App</strong> → your apps
              </>
            }
          />
        </>
      ) : null}
    </>
  )
}

/** One way to log — what it catches, Install, then its two steps after installing. */
function OptionPanel({
  title,
  what,
  shortcut,
  trigger,
}: {
  title: string
  what: string
  shortcut: SetupShortcut
  trigger: React.ReactNode
}) {
  return (
    <Panel title={title} subtitle={what}>
      <Stack gap="--space-5">
        <Install shortcut={shortcut} label="Install" />
        <List ordered>
          <li>Open the download, install the shortcut, paste your setup code</li>
          <li>{trigger}</li>
        </List>
      </Stack>
    </Panel>
  )
}

function CopyCode({ code }: { code: string }) {
  const [copied, setCopied] = useState<boolean | null>(null)

  async function copy() {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
    } catch {
      // navigator.clipboard needs a secure context, which a plain http:// Tailscale address
      // is not — fall back to a selection the user can copy.
      const box = document.getElementById('phone-setup-code') as HTMLTextAreaElement | null
      box?.focus()
      box?.select()
      setCopied(document.execCommand?.('copy') ?? false)
    }
  }

  return (
    <Stack gap="--space-4">
      <TextArea
        id="phone-setup-code"
        mono
        readOnly
        rows={3}
        value={code}
        aria-label="Setup code"
        onFocus={(e) => e.currentTarget.select()}
      />
      <Button variant="secondary" fullWidth className="tap-target" onClick={() => void copy()}>
        {copied === null ? 'Copy' : copied ? 'Copied' : 'Select the code above and copy it'}
      </Button>
    </Stack>
  )
}

function Install({
  shortcut,
  secondary = false,
  label,
}: {
  shortcut: SetupShortcut
  secondary?: boolean
  label?: string
}) {
  if (!shortcut.install_url) {
    return (
      <Text as="p" size="md" tone="muted">
        <Badge tone="warn">Not signed</Badge> {shortcut.name} isn’t signed in this build, so iOS
        would refuse it. Build it by hand — see “Log a spend from your phone” in Help.
      </Text>
    )
  }
  const href = shortcut.install_url
  return (
    <Button
      variant={secondary ? 'secondary' : 'primary'}
      fullWidth
      className="tap-target"
      onClick={() => openShortcutInstall(href)}
    >
      {label ?? `Install “${shortcut.name}”`}
    </Button>
  )
}
