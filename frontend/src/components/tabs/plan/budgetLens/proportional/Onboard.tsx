// budgetLens/proportional/Onboard.tsx — setting up 50/30/20: your monthly net income, then each
// group's share of it. The groups are the book's Zero-Based groups (owner, 2026-09-26: "same as
// Zero-Based"), so nothing is sorted up front; each starts at what it spent over the last three
// months, and whatever the shares leave is savings. Every share is optional.
import { useState } from 'react'
import { Button, Modal } from '../../../../ui'
import { getCurrencySymbol } from '../../../../../utils/format'
import { parseMoneyInput } from '../../planData'
import { Check, Spark } from '../MethodCard'
import type { ProportionalTargets } from '../types'
import { GROUP_COLORS, SAVE_COLOR, fmt } from './propMath'
import './propTable.css'

/** A group and its recent spend — the method's own draft (GET …/proportional/draft). */
export interface OnboardGroup {
  group_id: string
  name: string
  avg_spend: number
}

const Minus = () => (
  <svg
    width="11"
    height="11"
    viewBox="0 0 12 12"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    aria-hidden="true"
  >
    <path d="M2.5 6h7" />
  </svg>
)
const Plus = () => (
  <svg
    width="11"
    height="11"
    viewBox="0 0 12 12"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    aria-hidden="true"
  >
    <path d="M6 2.5v7M2.5 6h7" />
  </svg>
)

/** Each group's share of `income` from its recent spend, rounded to 5% — never past 100 in all. */
function suggest(groups: OnboardGroup[], income: number): Record<string, number> {
  const out: Record<string, number> = {}
  let left = 100
  for (const g of groups) {
    const pct = income > 0 ? Math.round(((g.avg_spend / income) * 100) / 5) * 5 : 0
    const take = Math.min(pct, left)
    if (take > 0) out[g.group_id] = take
    left -= take
  }
  return out
}

export function Onboard({
  profileIncome,
  groups,
  busy,
  error,
  onDone,
  onLucius,
  onClose,
}: {
  /** The Profile's monthly net income, when it has one — pre-filled so it's a confirmation. */
  profileIncome: number
  groups: OnboardGroup[]
  busy?: boolean
  error?: string | null
  onDone: (income: number, targets: ProportionalTargets, excluded: string[]) => void
  onLucius: () => void
  onClose: () => void
}) {
  const [step, setStep] = useState(0)
  // The income as typed (cents included); `inc` is its parsed value.
  const [incText, setIncText] = useState(profileIncome ? String(profileIncome) : '')
  const inc = parseMoneyInput(incText)
  const [shares, setShares] = useState<Record<string, number> | null>(null)
  // Groups ticked out of the split — their spend won't count against income or savings (a
  // pass-through, a one-off paid from savings), the same tick as Configure's (owner, 2026-09-28).
  const [out, setOut] = useState<Record<string, boolean>>({})
  const suggested = shares ?? suggest(groups, inc)
  const t = Object.fromEntries(Object.entries(suggested).filter(([id]) => !out[id]))
  const total = Object.values(t).reduce((a, b) => a + b, 0)
  const stepBy = (id: string, d: number) => {
    const next = Math.max(0, (t[id] ?? 0) + d)
    if (d > 0 && total + d > 100) return
    setShares({ ...t, [id]: next })
  }
  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={step === 0 ? 'What’s your monthly net income?' : 'How much should each group get?'}
    >
      <div className="pt-ob">
        <div className="pt-ob-dots" aria-hidden="true">
          <i className="on" />
          <i className={step === 1 ? 'on' : ''} />
        </div>
        {step === 0 ? (
          <>
            <span className="pt-inc pt-inc--xl">
              <b>{getCurrencySymbol()}</b>
              <input
                autoFocus
                value={incText}
                placeholder="0"
                inputMode="decimal"
                aria-label="Monthly take-home"
                onChange={(e) => setIncText(e.target.value.replace(/[^0-9.]/g, ''))}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && inc) setStep(1)
                }}
              />
            </span>
            <p className="pt-ob-sub">
              {profileIncome && inc === profileIncome
                ? 'From your Profile — change it if it’s out of date.'
                : 'A starting estimate — once your income transactions arrive, the budget uses those instead. Saved to your Profile.'}
            </p>
          </>
        ) : (
          <>
            <div className="pt-steps">
              {groups.map((g, i) => (
                <div
                  className={'pt-step' + (out[g.group_id] ? ' pt-step--out' : '')}
                  key={g.group_id}
                >
                  <i style={{ background: GROUP_COLORS[i % GROUP_COLORS.length] }} />
                  <span className="n">{g.name}</span>
                  <button
                    type="button"
                    aria-label={'Lower ' + g.name}
                    disabled={!!out[g.group_id]}
                    onClick={() => stepBy(g.group_id, -5)}
                  >
                    <Minus />
                  </button>
                  <span className="v">
                    {out[g.group_id] ? 'Excluded' : t[g.group_id] ? t[g.group_id] + '%' : '—'}
                  </span>
                  <button
                    type="button"
                    aria-label={'Raise ' + g.name}
                    disabled={!!out[g.group_id]}
                    onClick={() => stepBy(g.group_id, 5)}
                  >
                    <Plus />
                  </button>
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={!out[g.group_id]}
                    aria-label={'Count ' + g.name + ' in the split'}
                    title={out[g.group_id] ? 'Count in the split' : 'Exclude from the split'}
                    className={'bo-check pt-step-in' + (out[g.group_id] ? '' : ' on')}
                    onClick={() => setOut((m) => ({ ...m, [g.group_id]: !m[g.group_id] }))}
                  >
                    {!out[g.group_id] && <Check size={11} />}
                  </button>
                </div>
              ))}
              <div className="pt-step">
                <i style={{ background: SAVE_COLOR }} />
                <span className="n">Savings</span>
                <span className="v rest">
                  {100 - total}%{inc > 0 ? ' · ' + fmt((inc * (100 - total)) / 100) : ''}
                </span>
              </div>
            </div>
            <p className="pt-ob-sub">
              Your Zero-Based groups, each starting at what it spent over the last three months.
              Untick one to leave it out of the split. Change any of them here or later in
              Configure.
            </p>
          </>
        )}
        {error && (
          <p className="pt-ob-err" role="alert">
            {error}
          </p>
        )}
        <div className="pt-ob-foot">
          <Button
            variant="primary"
            size="sm"
            className="fi-lucius"
            iconLeft={<Spark size={15} />}
            onClick={onLucius}
            disabled={busy}
          >
            Let Lucius set it up
          </Button>
          <span className="bo-spacer" />
          {step === 1 && (
            <Button variant="secondary" size="sm" onClick={() => setStep(0)}>
              Back
            </Button>
          )}
          {step === 0 ? (
            <Button variant="primary" size="sm" disabled={!inc} onClick={() => setStep(1)}>
              Continue
            </Button>
          ) : (
            <Button
              variant="primary"
              size="sm"
              loading={busy}
              onClick={() =>
                onDone(
                  inc,
                  t,
                  Object.keys(out).filter((id) => out[id]),
                )
              }
            >
              Start budgeting
            </Button>
          )}
        </div>
      </div>
    </Modal>
  )
}
