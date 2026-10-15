import { useState } from 'react'
import type { KeyboardEvent } from 'react'
import { authFetch } from '../api/client'
import {
  Modal,
  Container,
  Stack,
  Cluster,
  FormField,
  TextField,
  PasswordField,
  NumberField,
  Select,
  Button,
  Text,
} from './ui'
import type { SelectOption } from './ui'
import { Spark } from '../lucius/identity'
import { ACCOUNT_TYPES, TYPE_LABELS, TYPE_MEANING } from './tabs/accounts/categories'

// First-run onboarding wizard (P3-ON / ON1). A stepped <Modal> composed entirely
// from the ui/ kit. Triggered by App.tsx's existing is_fresh / setup-status flow.
// Every step calls an EXISTING endpoint — no new backend surface is needed here.
//
// Completion / non-reappearance: finish() sets localStorage['onboarding-dismissed'].
// Writing a profile name / account / holding also flips is_fresh_setup() false; the
// localStorage flag covers the "started empty + skipped everything" case so the
// wizard doesn't re-pop on reload.

const CURRENCIES = [
  'USD',
  'SGD',
  'EUR',
  'GBP',
  'AUD',
  'CAD',
  'HKD',
  'JPY',
  'CNY',
  'INR',
  'MYR',
  'THB',
  'IDR',
  'VND',
]
const CURRENCY_OPTIONS: SelectOption[] = CURRENCIES.map((c) => ({ value: c, label: c }))

const ACCOUNT_TYPE_OPTIONS: SelectOption[] = ACCOUNT_TYPES.map((t) => ({
  value: t,
  label: `${TYPE_LABELS[t]} — ${TYPE_MEANING[t]}`,
}))

type StartChoice = 'empty' | 'demo'

// The wizard is a fixed 6-step sequence. The demo branch skips the first-account
// step (demo data already populates accounts), so progress is measured against the
// steps that are actually reachable on the chosen path — never a misleading
// "6 of 6" after only doing 5 things.
const STEP_TITLES = [
  'Welcome to Open Net Worth',
  'Set a password',
  'Connect your AI assistant',
  'Choose your base currency',
  'How do you want to start?',
  'Add your first account',
  "You're all set",
]
const STEP_WELCOME = 0
const STEP_PASSWORD = 1
const STEP_AI = 2
const STEP_CURRENCY = 3
const STEP_START = 4
const STEP_ACCOUNT = 5
const STEP_DONE = 6

const AI_PROVIDER_OPTIONS: SelectOption[] = [
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai', label: 'OpenAI (GPT)' },
  { value: 'ollama', label: 'Ollama (local, no key needed)' },
]

export interface OnboardingWizardProps {
  onDone: () => void
  onOpenChat: (prefill?: string) => void
  authRequired: boolean
  onAuthChanged: () => void
  /** Current profile base currency — the wizard's default (step 2). */
  defaultCurrency?: string
  /** Re-read the profile after the wizard writes base currency / demo data. */
  onProfileChanged: () => void
}

export function OnboardingWizard({
  onDone,
  onOpenChat,
  authRequired,
  onAuthChanged,
  defaultCurrency = 'USD',
  onProfileChanged,
}: OnboardingWizardProps) {
  const [step, setStep] = useState(STEP_WELCOME)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // True once the demo dataset was loaded — drives the progress dots (the
  // first-account step is no longer on this path) and the Done-step copy.
  const [demoLoaded, setDemoLoaded] = useState(false)

  // Step 1 — password
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')

  // Step 2 — AI provider
  const [aiProvider, setAiProvider] = useState('anthropic')
  const [aiKey, setAiKey] = useState('')
  const [aiSaving, setAiSaving] = useState(false)
  const [aiError, setAiError] = useState<string | null>(null)

  // Step 3 — base currency
  const [currency, setCurrency] = useState(defaultCurrency || 'USD')

  // Step 4 — start choice
  const [startChoice, setStartChoice] = useState<StartChoice>('empty')

  // Step 5 — first account
  const [acctName, setAcctName] = useState('')
  const [acctType, setAcctType] = useState('cash')
  const [acctBalance, setAcctBalance] = useState('')

  // Progress model. On the demo path the account step is skipped.
  const stepsOnPath = demoLoaded
    ? [STEP_WELCOME, STEP_PASSWORD, STEP_AI, STEP_CURRENCY, STEP_START, STEP_DONE]
    : [STEP_WELCOME, STEP_PASSWORD, STEP_AI, STEP_CURRENCY, STEP_START, STEP_ACCOUNT, STEP_DONE]
  const ordinal = Math.max(0, stepsOnPath.indexOf(step))

  function finish() {
    localStorage.setItem('onboarding-dismissed', '1')
    onDone()
  }

  function skipToChat() {
    finish()
    onOpenChat('Help me get set up — what should I do first?')
  }

  function goBack() {
    setError(null)
    const idx = stepsOnPath.indexOf(step)
    const prev = stepsOnPath[idx - 1]
    if (idx > 0 && prev !== undefined) setStep(prev)
  }

  // ── Step actions (each calls one existing endpoint) ──

  async function submitPassword() {
    setError(null)
    if (!password) {
      setError('Enter a password, or skip this step.')
      return
    }
    if (password.length < 8) {
      setError('Use at least 8 characters.')
      return
    }
    if (password !== confirm) {
      setError('Passwords do not match.')
      return
    }
    setBusy(true)
    try {
      const res = await authFetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_password: password }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => null)
        setError(data?.detail || `Could not set password (${res.status}). Try again.`)
        return
      }
      onAuthChanged() // re-check auth status so the SPA stays coherent
      setPassword('')
      setConfirm('')
      setStep(STEP_AI)
    } catch {
      setError('Could not reach the server. Check your connection and try again.')
    } finally {
      setBusy(false)
    }
  }

  function skipPassword() {
    setPassword('')
    setConfirm('')
    setError(null)
    setStep(STEP_AI)
  }

  async function submitAi() {
    setAiError(null)
    const needsKey = aiProvider !== 'ollama'
    if (needsKey && !aiKey.trim()) {
      setAiError('Paste your API key, or choose Ollama to skip.')
      return
    }
    setAiSaving(true)
    try {
      // Store per-provider credentials
      const credBody: Record<string, string> = {}
      if (aiKey.trim()) credBody.api_key = aiKey.trim()
      await authFetch(`/api/model-config/providers/${aiProvider}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credBody),
      })
      // Set as the instance default
      await authFetch('/api/model-config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: aiProvider }),
      })
      setAiKey('')
      setStep(STEP_CURRENCY)
    } catch {
      setAiError('Could not reach the server. Check your connection and try again.')
    } finally {
      setAiSaving(false)
    }
  }

  function skipAi() {
    setAiKey('')
    setAiError(null)
    setStep(STEP_CURRENCY)
  }

  async function submitCurrency() {
    setError(null)
    if (!currency.trim()) {
      setError('Pick a base currency.')
      return
    }
    setBusy(true)
    try {
      const res = await authFetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_currency: currency.trim().toUpperCase() }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => null)
        setError(data?.detail || `Could not save currency (${res.status}). Try again.`)
        return
      }
      onProfileChanged()
      setStep(STEP_START)
    } catch {
      setError('Could not reach the server. Check your connection and try again.')
    } finally {
      setBusy(false)
    }
  }

  async function submitStartChoice() {
    setError(null)
    if (startChoice === 'empty') {
      setDemoLoaded(false)
      setStep(STEP_ACCOUNT)
      return
    }
    setBusy(true)
    try {
      const res = await authFetch('/api/demo/seed', { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => null)
        setError(data?.detail || `Could not load demo data (${res.status}). Try again.`)
        return
      }
      onProfileChanged()
      // Demo populated accounts — skip the first-account step, go to Done.
      setDemoLoaded(true)
      setStep(STEP_DONE)
    } catch {
      setError('Could not reach the server. Check your connection and try again.')
    } finally {
      setBusy(false)
    }
  }

  async function submitAccount() {
    setError(null)
    if (!acctName.trim()) {
      setError('Give the account a name.')
      return
    }
    setBusy(true)
    try {
      const body: Record<string, unknown> = {
        name: acctName.trim(),
        type: acctType,
        currency: currency.trim().toUpperCase(),
      }
      const bal = parseFloat(acctBalance)
      if (!Number.isNaN(bal)) body.initial_balance = bal
      const res = await authFetch('/api/accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => null)
        setError(data?.detail || `Could not create account (${res.status}). Try again.`)
        return
      }
      setStep(STEP_DONE)
    } catch {
      setError('Could not reach the server. Check your connection and try again.')
    } finally {
      setBusy(false)
    }
  }

  // Allow Enter to submit on text/password fields — avoids forced mouse use.
  function onPasswordKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !busy) void submitPassword()
  }
  function onCurrencyKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !busy) void submitCurrency()
  }
  function onAccountKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !busy) void submitAccount()
  }

  const balanceLabel =
    acctType === 'credit'
      ? `Current balance owed (${currency.toUpperCase()})`
      : `Current balance (${currency.toUpperCase()})`

  // ── Step bodies ──

  const stepBody = () => {
    switch (step) {
      case STEP_WELCOME:
        return (
          <Stack gap="--space-5">
            <div className="onb-welcome-icon" aria-hidden="true">
              <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
                <circle cx="24" cy="24" r="24" fill="var(--accent, #3b86dd)" opacity="0.12" />
                <path
                  d="M14 28l6-6 4 4 10-10"
                  stroke="var(--accent, #3b86dd)"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  d="M24 16v-2M24 34v-2M16 24h-2M34 24h2"
                  stroke="var(--accent, #3b86dd)"
                  strokeWidth="2"
                  strokeLinecap="round"
                  opacity="0.5"
                />
              </svg>
            </div>
            <Text as="p" tone="muted">
              A self-hosted wealth manager that tracks your accounts, investments, spending, and net
              worth — all on your own machine.
            </Text>
            <Text as="p" tone="muted">
              This quick setup takes about a minute. You can change anything later, and Lucius (your
              AI assistant) can help with the rest in conversation.
            </Text>
          </Stack>
        )
      case STEP_PASSWORD:
        return (
          <Stack gap="--space-5">
            <Text as="p" tone="muted">
              Optional — only needed if you expose this instance to your network. Leave both fields
              blank to skip. You can add or change the password anytime in{' '}
              <Text as="span" tone="faint" size="sm">
                Settings &rsaquo; Security
              </Text>
              .
            </Text>
            <FormField label="New password" htmlFor="onb-pw" help="At least 8 characters.">
              <PasswordField
                id="onb-pw"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={onPasswordKeyDown}
                autoComplete="new-password"
                placeholder="Leave blank to skip"
              />
            </FormField>
            <FormField label="Confirm password" htmlFor="onb-pw2">
              <PasswordField
                id="onb-pw2"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                onKeyDown={onPasswordKeyDown}
                autoComplete="new-password"
                placeholder="Repeat the password above"
              />
            </FormField>
          </Stack>
        )
      case STEP_AI:
        return (
          <Stack gap="--space-5">
            <Text as="p" tone="muted">
              Lucius, your AI assistant, needs a language model to answer questions and help manage
              your finances. Pick a provider and paste your API key — or choose Ollama if you run a
              local model. You can change this later in{' '}
              <Text as="span" tone="faint" size="sm">
                Settings &rsaquo; AI Model
              </Text>
              .
            </Text>
            <FormField label="Provider" htmlFor="onb-ai-provider">
              <Select
                id="onb-ai-provider"
                options={AI_PROVIDER_OPTIONS}
                value={aiProvider}
                onChange={(e) => {
                  setAiProvider(e.target.value)
                  setAiKey('')
                  setAiError(null)
                }}
              />
            </FormField>
            {aiProvider !== 'ollama' && (
              <FormField
                label="API key"
                htmlFor="onb-ai-key"
                help="Kept on this machine, used only to call this provider."
              >
                <PasswordField
                  id="onb-ai-key"
                  autoComplete="off"
                  value={aiKey}
                  onChange={(e) => setAiKey(e.target.value)}
                  placeholder={aiProvider === 'anthropic' ? 'sk-ant-…' : 'sk-…'}
                />
              </FormField>
            )}
            {aiProvider === 'ollama' && (
              <Text as="p" size="sm" tone="faint">
                Ollama runs locally — no key needed. Make sure Ollama is running before you start
                chatting with Lucius.
              </Text>
            )}
            {aiError && (
              <Text as="p" size="sm" tone="bad">
                {aiError}
              </Text>
            )}
          </Stack>
        )
      case STEP_CURRENCY:
        return (
          <Stack gap="--space-5">
            <Text as="p" tone="muted">
              All net worth totals and reports display in this currency. Accounts in other
              currencies still convert automatically — you can change this later in your profile.
            </Text>
            <FormField label="Base currency" htmlFor="onb-ccy">
              <Select
                id="onb-ccy"
                options={CURRENCY_OPTIONS}
                value={currency}
                onChange={(e) => setCurrency(e.target.value)}
                onKeyDown={onCurrencyKeyDown}
              />
            </FormField>
          </Stack>
        )
      case STEP_START:
        return (
          <Stack gap="--space-6">
            <Text as="p" tone="muted">
              Start with your own data, or explore a pre-loaded demo first.
            </Text>
            <div className="onb-choice-cards">
              <button
                type="button"
                className={`onb-choice-card${startChoice === 'empty' ? ' is-selected' : ''}`}
                onClick={() => setStartChoice('empty')}
              >
                <Text size="sm" weight={600}>
                  Start empty
                </Text>
                <Text size="xs" tone="muted" as="p">
                  A blank slate. Add your own accounts, holdings, and transactions — or ask Lucius
                  to help.
                </Text>
              </button>
              <button
                type="button"
                className={`onb-choice-card${startChoice === 'demo' ? ' is-selected' : ''}`}
                onClick={() => setStartChoice('demo')}
              >
                <Cluster justify="between" align="center">
                  <Text size="sm" weight={600}>
                    Load demo data
                  </Text>
                  <span className="onb-choice-card__tag">Removable</span>
                </Cluster>
                <Text size="xs" tone="muted" as="p">
                  Explore a fully populated app with sample accounts, investments, budgets, and
                  transactions. Clear it anytime — your data is never touched.
                </Text>
              </button>
            </div>
          </Stack>
        )
      case STEP_ACCOUNT:
        return (
          <Stack gap="--space-5">
            <Text as="p" tone="muted">
              Add your first account to anchor your net worth — or skip and add accounts later via
              Lucius or the Accounts tab.
            </Text>
            <FormField label="Account name" htmlFor="onb-acct">
              <TextField
                id="onb-acct"
                value={acctName}
                onChange={(e) => setAcctName(e.target.value)}
                onKeyDown={onAccountKeyDown}
                placeholder="e.g. Everyday Checking"
                autoFocus
              />
            </FormField>
            <FormField label="Account type" htmlFor="onb-acct-type">
              <Select
                id="onb-acct-type"
                options={ACCOUNT_TYPE_OPTIONS}
                value={acctType}
                onChange={(e) => setAcctType(e.target.value)}
              />
            </FormField>
            <FormField
              label={balanceLabel}
              htmlFor="onb-acct-bal"
              help="Current balance. You can update it anytime."
            >
              <NumberField
                id="onb-acct-bal"
                value={acctBalance}
                onChange={(e) => setAcctBalance(e.target.value)}
                placeholder="0"
                step={0.01}
              />
            </FormField>
          </Stack>
        )
      case STEP_DONE:
        return (
          <Stack gap="--space-5">
            <div className="onb-done-icon" aria-hidden="true">
              <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
                <circle cx="24" cy="24" r="24" fill="var(--good, #34a853)" opacity="0.12" />
                <path
                  d="M16 24l6 6 10-12"
                  stroke="var(--good, #34a853)"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            {demoLoaded ? (
              <>
                <Text as="p" tone="muted">
                  Demo data is loaded. The dashboard shows sample accounts, investments, budgets,
                  and a few months of transactions so you can explore every feature.
                </Text>
                <Text as="p" tone="faint" size="sm">
                  When you&rsquo;re ready to use your own data, the{' '}
                  <Text as="span" weight={500}>
                    Clear demo &amp; start fresh
                  </Text>{' '}
                  banner at the top will wipe the sample data and reopen this wizard.
                </Text>
              </>
            ) : (
              <>
                <Text as="p" tone="muted">
                  You&rsquo;re set. Open the dashboard to see your net worth, or ask Lucius to help
                  you go further.
                </Text>
                <Text as="p" tone="faint" size="sm">
                  Suggested next steps: add more accounts, import a statement (
                  <Text as="span" weight={500}>
                    Settings → Data
                  </Text>
                  ), set a spending budget (Planning tab), or configure an AI provider (
                  <Text as="span" weight={500}>
                    Settings → AI Model
                  </Text>
                  ).
                </Text>
              </>
            )}
          </Stack>
        )
      default:
        return null
    }
  }

  // ── Footer (per-step actions) ──

  const back =
    step !== STEP_WELCOME && step !== STEP_DONE ? (
      <Button variant="ghost" onClick={goBack} disabled={busy}>
        Back
      </Button>
    ) : (
      <span />
    )

  const footer = () => {
    switch (step) {
      case STEP_WELCOME:
        return (
          <Cluster justify="between">
            <Button
              variant="ghost"
              onClick={skipToChat}
              title="Open the dashboard immediately and start chatting with Lucius"
            >
              Skip to dashboard
            </Button>
            <Button variant="primary" onClick={() => setStep(STEP_PASSWORD)}>
              Get started
            </Button>
          </Cluster>
        )
      case STEP_PASSWORD:
        return (
          <Cluster justify="between">
            {back}
            <Cluster>
              <Button variant="secondary" onClick={skipPassword} disabled={busy}>
                Skip — no password
              </Button>
              <Button variant="primary" onClick={submitPassword} loading={busy}>
                Set password &amp; continue
              </Button>
            </Cluster>
          </Cluster>
        )
      case STEP_AI:
        return (
          <Cluster justify="between">
            {back}
            <Cluster>
              <Button variant="secondary" onClick={skipAi} disabled={aiSaving}>
                Skip for now
              </Button>
              <Button variant="primary" onClick={submitAi} loading={aiSaving}>
                {aiProvider === 'ollama' ? 'Use Ollama' : 'Save & continue'}
              </Button>
            </Cluster>
          </Cluster>
        )
      case STEP_CURRENCY:
        return (
          <Cluster justify="between">
            {back}
            <Button variant="primary" onClick={submitCurrency} loading={busy}>
              Continue
            </Button>
          </Cluster>
        )
      case STEP_START:
        return (
          <Cluster justify="between">
            {back}
            <Button variant="primary" onClick={submitStartChoice} loading={busy}>
              {startChoice === 'demo' ? 'Load demo data' : 'Continue'}
            </Button>
          </Cluster>
        )
      case STEP_ACCOUNT:
        return (
          <Cluster justify="between">
            {back}
            <Cluster>
              <Button
                variant="secondary"
                onClick={() => setStep(STEP_DONE)}
                disabled={busy}
                title="You can add accounts later from the Accounts tab or by asking Lucius"
              >
                Skip for now
              </Button>
              <Button variant="primary" onClick={submitAccount} loading={busy}>
                Add account
              </Button>
            </Cluster>
          </Cluster>
        )
      case STEP_DONE:
        return (
          <Cluster justify="between">
            <Button
              variant="primary"
              className="fi-lucius"
              iconLeft={<Spark size={15} ariaHidden />}
              onClick={skipToChat}
              title="Open chat with Lucius pre-filled to help you get started"
            >
              Ask Lucius to help
            </Button>
            <Button variant="primary" onClick={finish}>
              Open dashboard
            </Button>
          </Cluster>
        )
      default:
        return null
    }
  }

  // Onboarding for an unconfigured instance whose user later mis-set a password
  // remotely: the password step copy explains it's optional. authRequired is read
  // only to keep the prop wired for parity with App's auth state.
  void authRequired

  return (
    <Modal
      open
      onClose={finish}
      title={STEP_TITLES[step]}
      size="lg"
      closeOnBackdrop={false}
      dragToDismiss={false}
      footer={
        <Container size="narrow">
          <Stack gap="--space-4">
            {error && (
              <Cluster gap="--space-2" align="center">
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 14 14"
                  fill="none"
                  aria-hidden="true"
                  style={{ flexShrink: 0 }}
                >
                  <circle cx="7" cy="7" r="6.5" stroke="var(--bad, #e05252)" />
                  <path
                    d="M7 4v3.5M7 9.5v.5"
                    stroke="var(--bad, #e05252)"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                  />
                </svg>
                <Text as="p" tone="bad" size="sm">
                  {error}
                </Text>
              </Cluster>
            )}
            {footer()}
          </Stack>
        </Container>
      }
    >
      <Container size="narrow">
        <Stack gap="--space-5">
          <div className="onb-progress" aria-hidden="true">
            {stepsOnPath.map((s, i) => (
              <span
                key={s}
                className={`onb-progress__dot${i === ordinal ? ' is-current' : ''}${
                  i < ordinal ? ' is-done' : ''
                }`}
              />
            ))}
          </div>
          <Text size="xs" tone="faint">
            Step {ordinal + 1} of {stepsOnPath.length}
          </Text>
          {stepBody()}
        </Stack>
      </Container>
    </Modal>
  )
}
