// plan/budgetActivity.tsx — ActivityCell: the Activity figure → a fixed-position popover
// listing the month's REAL transactions for this category (replaces the prototype's
// synthesized txnsFor). Account + payee names are resolved from a once-cached lookup.
import { useState, useRef, useEffect, useLayoutEffect } from 'react'
import { createPortal } from 'react-dom'
import * as PD from './planData'
import { authFetch } from '../../../api/client'
import { monthLabel } from '../../../hooks/useBudget'
import { fDate, getCurrencySymbol } from '../../../utils/format'
import { Button, Skeleton } from '../../ui'
import { X } from './icons'

const { fFig, fBaseCents, grp } = PD

// Explicit +/− sign, cents always shown — the YNAB-reference breakdown box's own convention
// (distinct from fSigned/fBaseCents elsewhere: n===0 renders "−$0.00", matching the reference's
// literal "Returns −$0.00" rather than this app's usual "0 is unsigned" treatment).
function fSignedCents(n: number): string {
  return (n > 0 ? '+' : '−') + getCurrencySymbol() + grp(n, true)
}

// the raw txn JSON straight off the API (full shape) — passed up to open the detail drawer
export type RawTxn = Record<string, unknown>
interface TxnRow {
  account: string
  date: string
  payee: string
  memo: string
  amount: number
  kind: string
  raw: RawTxn
}

// payment_activity_breakdown's shape (planning.py) — the two-column Activity-popover box for a
// Credit-Card-Payments envelope. Present in the /activity-transactions response only when the
// fetch resolves to a live payment envelope.
export interface PaymentBreakdown {
  spending: number
  returns: number
  total_spending: number
  funded_spending: number
  payments_returns: number
  total_activity: number
}

// The minimal shape ActivityCell needs — a real LocalCat satisfies this structurally (its
// `categoryIds` are the member categories to fetch), and the derived Uncategorized row (no real
// category — budget.tsx builds a small literal) opts into the `uncategorized` fetch instead.
export interface ActivityCellTarget {
  activity: number
  label: string
  color: string
  /** Real category keys this row's activity is drawn from — repeated `category=` params
   *  (a merged N>1 envelope row's activity is the SUM across every member). */
  categoryIds?: string[]
  /** The backing envelope id. For a ZERO-category envelope (a Credit Card Payments / Commitments
   *  synced envelope), `categoryIds` is empty but its activity is keyed by the envelope id itself
   *  (`_iter_category_contributions` folds card credits + payments against it), so we fetch by
   *  this id — else the popover shows "0 transactions" while the Activity total is nonzero. */
  envelopeId?: string
  /** The derived Uncategorized spending / income rows — fetch `?uncategorized=true` instead
   *  of `?category=`. */
  uncategorized?: boolean
  /** Composes with `uncategorized` (handoff item 7 — the Outside envelopes "Uncategorized
   *  income" row): `?uncategorized=true&kind=income` isolates uncategorized INCOME from
   *  uncategorized EXPENSE (Uncategorized) — both share the same blank-category bucket server-side. */
  kind?: string
  /** A Credit-Card-Payments envelope (LocalCat's own field — see useBudgetModel) — swaps the
   *  popover to the YNAB-reference breakdown box + card-leg-amount signs (see the fetch effect
   *  and render below). Every other row is byte-identical to before. */
  isPaymentEnvelope?: boolean
}

// Pure — the GET /api/planning/activity-transactions querystring for one row·month. Split out
// for vitest coverage (a live fetch isn't worth mounting the component for) and reused verbatim
// by the effect below. A real row fetches every member category (repeated `category=` params);
// the derived Uncategorized spending/income row fetches the blank-category sentinel
// (`category=` with an empty value, i.e. `[""]` server-side) instead, optionally narrowed by
// `kind` — both share the "" bucket in `_activity_by_category`.
export function buildActivityQuery(c: ActivityCellTarget, month: string): string {
  const p = new URLSearchParams({ ym: month })
  if (c.uncategorized) {
    p.append('category', '')
    if (c.kind) p.append('kind', c.kind)
  } else {
    // A zero-category envelope (payment/commitments) has no member categories — its activity is
    // keyed by the envelope id, so fetch by that instead of sending no `category` at all.
    const keys =
      c.categoryIds && c.categoryIds.length ? c.categoryIds : c.envelopeId ? [c.envelopeId] : []
    for (const key of keys) p.append('category', key)
  }
  return p.toString()
}

// the register-filter shape RouterShell/AccountsTab already understand (budget.tsx's
// RegisterFilter) — a structural subset kept local to dodge a budget.tsx↔budgetActivity.tsx
// import cycle (budget.tsx itself imports ActivityCell). `category` is now `string | string[]`
// (FEB-07) — the register/tokenFilters layer already ORs repeated category tokens, so a merged
// row's deep-link can carry every member instead of silently narrowing to just the primary one.
export interface ActivityRegisterFilter {
  category?: string | string[]
  month?: string
  uncategorized?: boolean
  kind?: string
}

// Pure — the popover's "Open in Accounts" deep-link filter (handoff item 14, FEB-07 fix). A
// merged (N>1) envelope row's activity is the SUM across every member category — the deep-link
// now carries ALL of them (a single string for the common 1:1 case, an array for N>1) so the
// register shows the SAME set of transactions the popover just listed, never a subset.
export function buildRegisterFilter(c: ActivityCellTarget, month: string): ActivityRegisterFilter {
  if (c.uncategorized) return { uncategorized: true, month, ...(c.kind ? { kind: c.kind } : {}) }
  const keys = c.categoryIds ?? []
  return { category: keys.length <= 1 ? keys[0] : keys, month }
}

// The YNAB-reference two-column box (owner-provided screenshot) for a Credit-Card-Payments
// envelope's Activity popover: left = what actually posted to the CARD ACCOUNT this month
// (Spending/Returns/Total Spending, uncapped by envelope coverage); right = the envelope's own
// Activity fold (Funded Spending/Payments & Returns/Total Activity — sums to `total_activity`,
// which equals the row's own Activity total shown in the footer below). Pure display — no
// money math lives here, all six figures arrive precomputed from `payment_activity_breakdown`.
function BdRow({ label, value, total }: { label: string; value: number; total?: boolean }) {
  return (
    <div className={'pl-actpop-bd-row' + (total ? ' pl-actpop-bd-row--total' : '')}>
      <span className="k">{label}</span>
      <span className={'v mask' + (value > 0 ? ' pos' : value < 0 ? ' neg' : '')}>
        {fSignedCents(value)}
      </span>
    </div>
  )
}
export function PaymentBreakdownBox({ b }: { b: PaymentBreakdown }) {
  return (
    <div className="pl-actpop-bd">
      <div className="pl-actpop-bd-col">
        <BdRow label="Spending" value={b.spending} />
        <BdRow label="Returns" value={b.returns} />
        <BdRow label="Total Spending" value={b.total_spending} total />
      </div>
      <div className="pl-actpop-bd-col">
        <BdRow label="Funded Spending" value={b.funded_spending} />
        <BdRow label="Payments & Returns" value={b.payments_returns} />
        <BdRow label="Total Activity" value={b.total_activity} total />
      </div>
    </div>
  )
}

export function ActivityCell({
  c,
  month,
  onOpenTxn,
  onOpenRegister,
}: {
  c: ActivityCellTarget
  month: string
  onOpenTxn?: (raw: RawTxn) => void
  /** "Open in Accounts" (handoff item 14) — deep-links the register to this row's
   *  category(-ies)+month, reusing the SAME callback Outside-envelopes' own row buttons use. */
  onOpenRegister?: (filter: ActivityRegisterFilter) => void
}) {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<TxnRow[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [excludedCount, setExcludedCount] = useState(0)
  const [breakdown, setBreakdown] = useState<PaymentBreakdown | null>(null)
  const [pos, setPos] = useState<{
    left: number
    W: number
    arrowX: number
    below: boolean
    top: number | null
    bottom: number | null
    maxH: number
    root: Element
  } | null>(null)
  const ref = useRef<HTMLSpanElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)

  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    const r = btnRef.current!.getBoundingClientRect()
    // Wide enough for the Memo column to read (owner, 2026-09-25).
    const W = Math.min(660, window.innerWidth - 24)
    let left = Math.round(r.left + r.width / 2 - W / 2)
    left = Math.max(12, Math.min(left, window.innerWidth - W - 12))
    const below = window.innerHeight - r.bottom > 260
    const arrowX = Math.max(16, Math.min(W - 28, Math.round(r.left + r.width / 2 - left)))
    const top = below ? Math.round(r.bottom + 10) : null
    const bottom = below ? null : Math.round(window.innerHeight - r.top + 10)
    const rawMaxH = below ? window.innerHeight - (r.bottom + 10) - 12 : r.top - 10 - 12
    const maxH = Math.max(200, Math.min(window.innerHeight - 20, Math.round(rawMaxH)))
    // Picked here, not during render: the portal target (the modal the trigger sits in, else the
    // app shell) is read off the trigger's DOM, which render may not touch.
    const root =
      ref.current?.closest('.pl-modal') ?? document.querySelector('.app-shell') ?? document.body
    setPos({ left, W, arrowX, below, top, bottom, maxH, root })
  }, [open])

  // A merged (N>1) envelope row fetches EVERY member category (repeated `category=` params —
  // the backend ORs them) so the popover's rows sum to the same folded total the footer already
  // shows; previously only the row's primary category was fetched, so the list silently
  // contradicted the net figure for any merged row.
  const activityQuery = buildActivityQuery(c, month)

  // fetch the transactions that actually moved this row's Activity for this row·month when
  // opened — /api/planning/activity-transactions mirrors the SAME on-budget/cutoff/split/XOR
  // eligibility `_activity_by_category` folds into `c.activity`, so this list (and its signed
  // per-row amount) can never silently disagree with the net total below. A plain
  // `/api/transactions?category=` fetch (the old source) has no such awareness — it happily
  // lists a transaction that got excluded from Activity (e.g. dated before its account's
  // tracked-balance start), which is indistinguishable from a real contributor once shown.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    // Rows arrive labelled (payee_name, from/to_account_name) — no account or payee list fetch.
    authFetch(`/api/planning/activity-transactions?${activityQuery}`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((data) => {
        if (cancelled) return
        setFailed(data == null)
        const items: RawTxn[] = Array.isArray(data?.transactions) ? data.transactions : []
        const mapped: TxnRow[] = items.map((t) => {
          // A transfer has no merchant payee — render it YNAB-style as "Transfer : <the OTHER
          // account>". In the card-centric popover `txn_type` marks which leg is the card (payment →
          // the card is the TO leg, so the counterparty is FROM); elsewhere fall back to the to/from leg.
          const fromName = (t.from_account_name as string) || ''
          const toName = (t.to_account_name as string) || ''
          const otherName =
            t.txn_type === 'payment'
              ? fromName
              : t.txn_type === 'spend'
                ? toName
                : toName || fromName
          return {
            account: fromName || toName || '—',
            date: (t.date as string) || '',
            payee:
              t.kind === 'transfer'
                ? `Transfer : ${otherName || '—'}`
                : (t.payee_name as string) || '—',
            memo: (t.activity_memo as string) || '',
            // A payment-envelope row shows the CARD ACCOUNT'S OWN leg (YNAB sign: payment/return +,
            // spend −) via `card_leg_amount` — every other row keeps `activity_amount` (SIGNED, the
            // category-scoped contribution — a split's own fraction, never the full leg).
            amount:
              c.isPaymentEnvelope && typeof t.card_leg_amount === 'number'
                ? (t.card_leg_amount as number)
                : (t.activity_amount as number) || 0,
            kind: (t.kind as string) || 'expense',
            raw: t,
          }
        })
        setRows(mapped)
        setExcludedCount(typeof data?.excluded_count === 'number' ? data.excluded_count : 0)
        setBreakdown((data?.breakdown as PaymentBreakdown) || null)
      })
    return () => {
      cancelled = true
    }
  }, [open, activityQuery, c.isPaymentEnvelope])

  useEffect(() => {
    if (!open) return
    // The panel is portaled out of the table (below), so "inside" is the trigger OR the panel.
    const inside = (t: Node) => !!(ref.current?.contains(t) || popRef.current?.contains(t))
    const h = (e: MouseEvent) => {
      if (!inside(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    const onScroll = (e: Event) => {
      if (inside(e.target as Node)) return
      setOpen(false)
    }
    const close = () => setOpen(false)
    document.addEventListener('mousedown', h)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', h)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open])

  // Always clickable — even a net-ZERO row can have real transactions that cancel (e.g. a
  // proxy loan paid AND repaid in the same month). Hiding those behind a bare "0" was the
  // bug: the popover must open so you can see the offsetting legs. Genuinely-empty rows just
  // show "No transactions this month".
  return (
    <span className="pl-act-wrap" ref={ref}>
      <button
        ref={btnRef}
        className="pl-alink mask"
        title="View transactions"
        onClick={(e) => {
          e.stopPropagation()
          // Every open starts in the loading state — a reopened row used to show its previous
          // result (or "No transactions this month.") until the new fetch landed.
          if (!open) {
            setRows(null)
            setFailed(false)
            setExcludedCount(0)
            setBreakdown(null)
          }
          setOpen((o) => !o)
        }}
      >
        {fFig(c.activity).text}
      </button>
      {/* Portaled to the app shell: the desktop table's content carries a transform (the
          workspace scroll hold, plan.css), which would otherwise capture this fixed panel —
          positioning it against the table instead of the viewport and clipping it. */}
      {open &&
        pos &&
        createPortal(
          <div
            ref={popRef}
            data-modal-escape-layer
            className="pl-actpop"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'fixed',
              left: pos.left + 'px',
              top: pos.top != null ? pos.top + 'px' : 'auto',
              bottom: pos.bottom != null ? pos.bottom + 'px' : 'auto',
              width: pos.W + 'px',
              maxHeight: pos.maxH + 'px',
            }}
          >
            <span
              className="pl-actpop-arrow"
              style={{
                left: pos.arrowX + 'px',
                [pos.below ? 'top' : 'bottom']: '-6px',
                transform: pos.below ? 'rotate(45deg)' : 'rotate(225deg)',
              }}
            />
            <div className="pl-actpop-head">
              <div>
                <div className="pl-actpop-title">Activity</div>
                <div className="pl-actpop-sub">
                  <i style={{ background: c.color }} /> {c.label} • {monthLabel(month)}
                </div>
              </div>
              <button className="pl-actpop-x" onClick={() => setOpen(false)} aria-label="Close">
                <X />
              </button>
            </div>
            {/* One scroll area for the breakdown and the transactions (owner, 2026-09-25): the
                table's header and rows used to be pinned inside the flex column, so a short
                popover had room for the header and not one row. Title and footer stay put. */}
            <div className="pl-actpop-scroll">
              {c.isPaymentEnvelope && breakdown && <PaymentBreakdownBox b={breakdown} />}
              <div
                className={
                  'pl-actpop-table' + (c.isPaymentEnvelope ? ' pl-actpop-table--no-account' : '')
                }
              >
                <div className="pl-actpop-thead">
                  {!c.isPaymentEnvelope && <span>Account</span>}
                  <span>Date</span>
                  <span>Payee</span>
                  <span>Memo</span>
                  <span className="r">Amount</span>
                </div>
                <div className="pl-actpop-rows">
                  {rows == null ? (
                    <div role="status" aria-label="Loading transactions">
                      {[0, 1, 2].map((i) => (
                        <div
                          key={i}
                          className="pl-actpop-row pl-actpop-row--skel"
                          aria-hidden="true"
                        >
                          {!c.isPaymentEnvelope && <Skeleton height={12} width="70%" />}
                          <Skeleton height={12} width="75%" />
                          <Skeleton height={12} width="65%" />
                          <Skeleton height={12} width="55%" />
                          <span className="r">
                            <Skeleton height={12} width={56} />
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : failed ? (
                    <div className="pl-actpop-loading">Couldn't load these transactions.</div>
                  ) : rows.length === 0 ? (
                    <div className="pl-actpop-loading">No transactions this month.</div>
                  ) : (
                    rows.map((t, i) => (
                      <div
                        key={i}
                        className={'pl-actpop-row' + (onOpenTxn ? ' clickable' : '')}
                        role={onOpenTxn ? 'button' : undefined}
                        tabIndex={onOpenTxn ? 0 : undefined}
                        onClick={
                          onOpenTxn
                            ? () => {
                                onOpenTxn(t.raw)
                                setOpen(false)
                              }
                            : undefined
                        }
                        onKeyDown={
                          onOpenTxn
                            ? (e) => {
                                if (e.key === 'Enter') {
                                  onOpenTxn(t.raw)
                                  setOpen(false)
                                }
                              }
                            : undefined
                        }
                        title={onOpenTxn ? 'Open transaction' : undefined}
                      >
                        {!c.isPaymentEnvelope && <span title={t.account}>{t.account}</span>}
                        <span>{fDate(t.date, 'numeric')}</span>
                        <span title={t.payee}>{t.payee}</span>
                        <span className="memo" title={t.memo}>
                          {t.memo}
                        </span>
                        <span className={'r mask' + (t.amount >= 0 ? ' pos' : ' neg')}>
                          {t.amount >= 0 ? '+' : '−'}
                          {fBaseCents(Math.abs(t.amount))}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
            <div className="pl-actpop-foot">
              <span className="pl-actpop-total">
                {rows == null
                  ? 'Loading transactions…'
                  : `${rows.length} transaction${rows.length === 1 ? '' : 's'}`}{' '}
                • net <b className="mask">{fFig(c.activity).text}</b>
                {excludedCount > 0 && (
                  <span
                    className="pl-actpop-excluded"
                    title="Dated before that account's tracked-balance start, or otherwise not on-budget — not counted toward Activity."
                  >
                    {' '}
                    • {excludedCount} more this month didn't count
                  </span>
                )}
              </span>
              <span style={{ display: 'inline-flex', gap: 8 }}>
                {onOpenRegister && (
                  <Button
                    variant="secondary"
                    size="dense"
                    onClick={() => {
                      setOpen(false)
                      onOpenRegister(buildRegisterFilter(c, month))
                    }}
                  >
                    Open in Accounts
                  </Button>
                )}
                <Button variant="primary" size="dense" onClick={() => setOpen(false)}>
                  Close
                </Button>
              </span>
            </div>
          </div>,
          pos.root,
        )}
    </span>
  )
}
