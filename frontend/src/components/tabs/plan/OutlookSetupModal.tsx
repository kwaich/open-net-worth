// The Outlook's missing answers, asked in a dialog laid over the Outlook content area.
// It cannot be dismissed (no outlook exists without them) but it is not a page-wide overlay:
// the Plan tabs, the sidebar and Lucius stay usable, and it goes away on its own once the
// answers are saved and the Outlook has what it needs.
import { useId, useState } from 'react'
import { authFetch } from '../../../api/client'
import { PROFILE_KEY } from '../../../hooks/serverState'
import { Spark } from '../../../lucius/identity'
import { invalidateQueryCache, setCachedQueryData } from '../../../state/queryCache'
import { sendNextPrefill } from '../../../state/luciusSendIntent'
import { fNum, getCurrencySymbol } from '../../../utils/format'
import { Button, DateField, FormField, MoneyField } from '../../ui'
import '../../intro/intro.css'
import { useSeenIntro } from '../../intro/useSeenIntro'
import { EMPTY_FIRE_DRAFT, saveFireDraft } from './fireSettings'

const LUCIUS_ASK =
  'Help me set up my retirement outlook: ask my birth date, how much I save each month now and how much I expect to spend each month in retirement, save them, then walk me through my outlook. Ask me one thing at a time.'

const todayIso = () => new Date().toISOString().slice(0, 10)

export function OutlookSetupModal({
  suggestedSpending,
  needsBirthDate: needsBirthDateNow,
  needsSpending: needsSpendingNow,
  needsSaving: needsSavingNow = false,
  onSaved,
  onAskLucius,
}: {
  /** Today's monthly spending, 0 when unknown: what the retirement figure starts from. */
  suggestedSpending: number
  needsBirthDate: boolean
  needsSpending: boolean
  /** Nothing says what they put aside before retiring. The projection grows the pot by that
   *  saving, so without it every outlook reads as spending the pot down from today. */
  needsSaving?: boolean
  onSaved: () => void
  onAskLucius: (text: string) => void
}) {
  const id = useId()
  const [birthDate, setBirthDate] = useState('')
  const [spending, setSpending] = useState<number | null>(
    suggestedSpending > 0 ? suggestedSpending : null,
  )
  const [spendingUnreadable, setSpendingUnreadable] = useState(false)
  // The field commits its number on blur; the button follows the typing so it never looks dead
  // while an amount is already in the box.
  const [spendingTyped, setSpendingTyped] = useState<boolean | null>(null)
  const [monthlySaving, setMonthlySaving] = useState<number | null>(null)
  const [savingUnreadable, setSavingUnreadable] = useState(false)
  const [savingTyped, setSavingTyped] = useState<boolean | null>(null)
  // Answering these IS the Outlook's introduction: its intro must not open right after.
  const { markSeen: markIntroSeen } = useSeenIntro('plan-outlook')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The questions hold still while a save is in flight: the first answer landing refreshes the
  // profile, and the dialog must not drop a field and reword itself mid-save.
  const [asked, setAsked] = useState({
    birth: needsBirthDateNow,
    spend: needsSpendingNow,
    save: needsSavingNow,
  })
  if (
    !saving &&
    (asked.birth !== needsBirthDateNow ||
      asked.spend !== needsSpendingNow ||
      asked.save !== needsSavingNow)
  ) {
    setAsked({ birth: needsBirthDateNow, spend: needsSpendingNow, save: needsSavingNow })
  }
  const needsBirthDate = asked.birth
  const needsSpending = asked.spend
  const needsSaving = asked.save
  const answers = [needsBirthDate, needsSaving, needsSpending].filter(Boolean).length

  const birthDateOk = !needsBirthDate || (birthDate !== '' && birthDate < todayIso())
  const spendingOk =
    !needsSpending ||
    (!spendingUnreadable && (spendingTyped ?? (spending !== null && spending > 0)))
  // Zero is a real answer here: some people save nothing yet.
  const savingOk = !needsSaving || (!savingUnreadable && (savingTyped ?? monthlySaving !== null))
  const valid = birthDateOk && spendingOk && savingOk

  async function save() {
    setSaving(true)
    setError(null)
    try {
      if (needsBirthDate) {
        const res = await authFetch(PROFILE_KEY, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ date_of_birth: birthDate }),
        })
        if (!res.ok) {
          setError(`Could not save your birth date (HTTP ${res.status}).`)
          return
        }
        const saved = (await res.json()) as { profile: Record<string, unknown> }
        setCachedQueryData(PROFILE_KEY, saved.profile)
        invalidateQueryCache('/api/cashflow')
        invalidateQueryCache('/api/fire')
      }
      if (needsSpending || needsSaving) {
        const ok = await saveFireDraft({
          ...EMPTY_FIRE_DRAFT,
          monthlyRetirementSpending: needsSpending ? (spending ?? '') : '',
          monthlySaving: needsSaving ? (monthlySaving ?? 0) : '',
        })
        if (!ok) {
          setError('Could not save your answers. Try again.')
          return
        }
      }
      markIntroSeen()
      onSaved()
    } catch {
      setError('Could not reach the server. Try again.')
    } finally {
      setSaving(false)
    }
  }

  function askLucius() {
    sendNextPrefill()
    onAskLucius(LUCIUS_ASK)
  }

  const gradient = `${id}-fan`
  return (
    <div className="pl-os">
      <div className="pl-os-ghost" aria-hidden="true">
        <div className="pl-os-ghost-main">
          <div className="pl-os-ghost-card pl-os-ghost-card--head">
            <span className="pl-os-bar pl-os-bar--s" />
            <span className="pl-os-bar pl-os-bar--xl" />
            <span className="pl-os-bar pl-os-bar--l" />
          </div>
          <div className="pl-os-ghost-card pl-os-ghost-card--chart">
            <svg viewBox="0 0 400 150" preserveAspectRatio="none">
              <path
                d="M0 140 C90 125 160 80 240 30 C290 10 330 30 400 80 L400 140 C330 140 290 115 240 90 C160 110 90 135 0 145Z"
                fill="var(--primary)"
                fillOpacity="0.5"
              />
              <path
                d="M0 142 C90 130 160 95 240 58 C290 38 330 52 400 104"
                fill="none"
                stroke="var(--primary)"
                strokeWidth="2"
              />
            </svg>
          </div>
        </div>
        <div className="pl-os-ghost-card pl-os-ghost-rail">
          {[62, 40, 30, 55].map((at) => (
            <div key={at} className="pl-os-lever">
              <span className="pl-os-bar pl-os-bar--m" />
              <span className="pl-os-track">
                <i style={{ left: `${at}%` }} />
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="pl-os-card" role="dialog" aria-labelledby={`${id}-title`}>
        <div className="pl-os-hero" aria-hidden="true">
          <svg viewBox="0 0 440 150" preserveAspectRatio="none">
            <defs>
              <linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1">
                <stop offset="0" stopColor="var(--primary)" stopOpacity="0.3" />
                <stop offset="1" stopColor="var(--primary)" stopOpacity="0" />
              </linearGradient>
            </defs>
            <path
              d="M0 140 C90 128 160 90 250 38 C295 14 330 18 440 66 L440 150 L0 150 Z"
              fill={`url(#${gradient})`}
            />
            <path
              d="M0 144 C90 132 165 100 250 60 C300 38 340 52 440 100"
              fill="none"
              stroke="var(--primary)"
              strokeOpacity="0.7"
              strokeWidth="2"
              strokeDasharray="5 6"
            />
            <line
              x1="250"
              y1="0"
              x2="250"
              y2="150"
              stroke="var(--muted)"
              strokeOpacity="0.35"
              strokeDasharray="3 4"
            />
          </svg>
          <div className="pl-os-q">
            <span className="pl-os-q-pct">?%</span>
            <span className="pl-os-q-label">chance your money lasts</span>
          </div>
        </div>

        <div className="pl-os-body">
          <h2 id={`${id}-title`} className="pl-os-title">
            Will your money last?
          </h2>
          <p className="pl-os-sub">
            {['One answer', 'Two answers', 'Three answers'][answers - 1] ?? 'A few answers'} and
            you'll see when you could retire.
          </p>
          {needsBirthDate && (
            <FormField label="Your birth date" htmlFor={`${id}-dob`}>
              <DateField
                id={`${id}-dob`}
                value={birthDate}
                max={todayIso()}
                onChange={(e) => setBirthDate(e.target.value)}
              />
            </FormField>
          )}
          {needsSaving && (
            <FormField
              label="Monthly saving now"
              htmlFor={`${id}-save`}
              help="What you put aside each month until you retire. 0 is fine."
            >
              <MoneyField
                id={`${id}-save`}
                allowEmpty
                prefix={getCurrencySymbol()}
                value={monthlySaving}
                formattedValue={monthlySaving === null ? '' : fNum(monthlySaving)}
                onValueChange={setMonthlySaving}
                onInvalidChange={setSavingUnreadable}
                onInput={(e) => setSavingTyped(/\d/.test(e.currentTarget.value))}
              />
            </FormField>
          )}
          {needsSpending && (
            <FormField
              label="Monthly spending in retirement"
              htmlFor={`${id}-spend`}
              help={
                suggestedSpending > 0
                  ? 'About what you spend today. Change it if retirement looks different.'
                  : 'What you expect to spend each month once you stop working.'
              }
            >
              <MoneyField
                id={`${id}-spend`}
                allowEmpty
                prefix={getCurrencySymbol()}
                value={spending}
                formattedValue={spending === null ? '' : fNum(spending)}
                onValueChange={setSpending}
                onInvalidChange={setSpendingUnreadable}
                onInput={(e) => setSpendingTyped(/[1-9]/.test(e.currentTarget.value))}
              />
            </FormField>
          )}
          {error && (
            <p className="pl-os-error" role="alert">
              {error}
            </p>
          )}
        </div>

        <div className="pl-os-foot">
          <Button
            size="sm"
            variant="primary"
            className="fi-lucius"
            iconLeft={<Spark size={15} ariaHidden />}
            onClick={askLucius}
          >
            Set up with Lucius
          </Button>
          <Button size="sm" variant="primary" disabled={!valid} loading={saving} onClick={save}>
            Show my outlook
          </Button>
        </div>
      </div>
    </div>
  )
}
