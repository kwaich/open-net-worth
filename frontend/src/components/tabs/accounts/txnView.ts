/**
 * txnView.ts — shared derived-display computation for a register transaction.
 *
 * Factored out of TxnRow so the desktop grid row and the mobile TxnCard (Register
 * reflow, mobile-responsive-transactions) compute amount sign/currency, the payee
 * headline, and the mention sublabel identically — one definition, never two
 * copies that can silently diverge.
 */

import { getBaseCurrency, txnMentionSublabel } from '../../../utils/format'
import type { Txn, Account, FxRates } from './types'
import { fCur, fxInfo, toBase } from './format'

export interface TxnView {
  amount: number
  ccy: string
  pos: boolean
  baseVal: number
  isForeign: boolean
  baseStr: string
  fxText: string
  amtTitle: string | undefined
  nativeMain: string
  baseMain: string
  primaryAcctId: string | null | undefined
  primaryAcct: Account | undefined
  isTransfer: boolean
  isAdjustment: boolean
  isSplit: boolean
  xccyOther: { amt: number | null | undefined; ccy: string; sent: boolean } | null
  /** Non-transfer payee headline (payee name, else description, else em-dash). */
  payeeText: string
  /** Transfer-only from/to account names (null when unresolved). */
  payeeFromName: string | null
  payeeToName: string | null
  mentionSub: string
}

/** Everything TxnRow/TxnCard need to render a transaction, derived once. */
export function buildTxnView(
  t: Txn,
  accounts: Account[],
  selectedId: string | null,
  payeeName: string | null,
  rates: FxRates,
  showAccount: boolean,
): TxnView {
  const acctMap = Object.fromEntries(accounts.map((a) => [a.id, a]))
  const isTransfer = t.kind === 'transfer' || t.kind === 'adjustment'
  const base = getBaseCurrency()

  // Amount + sign for the row, relative to selected account or global. See TxnRow's original
  // comment for the two-leg model rationale — kept verbatim here.
  let amount = 0,
    ccy = base,
    pos = false
  const inflowLeg = () => {
    amount = t.toAmount ?? t.fromAmount ?? 0
    ccy = t.toCurrency ?? t.fromCurrency ?? base
    pos = true
  }
  const outflowLeg = () => {
    amount = t.fromAmount ?? t.toAmount ?? 0
    ccy = t.fromCurrency ?? t.toCurrency ?? base
    pos = false
  }
  if (selectedId && t.fromAccountId === selectedId) {
    amount = t.fromAmount ?? 0
    ccy = t.fromCurrency ?? base
    pos = false
  } else if (selectedId && t.toAccountId === selectedId) {
    amount = t.toAmount ?? t.fromAmount ?? 0
    ccy = t.toCurrency ?? t.fromCurrency ?? base
    pos = true
  } else if (t.kind === 'income' || (!t.fromAccountId && !!t.toAccountId)) {
    inflowLeg()
  } else {
    outflowLeg()
  }

  const baseVal =
    t.baseCurrency === base && t.baseAmount != null ? t.baseAmount : toBase(amount, ccy, rates)
  const isForeign = ccy !== base
  const fx = fxInfo(amount, ccy, t.origAmount, t.origCurrency, rates)
  const baseStr = fCur(Math.abs(baseVal), base, { cents: true })
  const fxText = [fx.fact, fx.est && fx.est + ' • ≈ today'].filter(Boolean).join(' • ')
  const amtTitle = (() => {
    const parts: string[] = []
    if (!showAccount && isForeign) parts.push('≈ ' + baseStr)
    if (fxText) parts.push(fxText)
    return parts.length ? parts.join(' • ') : undefined
  })()
  const sgn = pos ? '+' : '−'
  const nativeMain = sgn + fCur(Math.abs(amount), ccy, { cents: true })
  const baseMain = sgn + fCur(Math.abs(baseVal), base, { cents: true })
  const primaryAcctId = t.kind === 'income' ? t.toAccountId : t.fromAccountId || t.toAccountId
  const primaryAcct = primaryAcctId ? acctMap[primaryAcctId] : undefined
  const isSplit = t.category === 'split' && !!t.splits && t.splits.length > 0
  const xccyOther =
    t.kind === 'transfer' && t.fromCurrency && t.toCurrency && t.fromCurrency !== t.toCurrency
      ? pos
        ? { amt: t.fromAmount, ccy: t.fromCurrency, sent: true }
        : { amt: t.toAmount, ccy: t.toCurrency, sent: false }
      : null

  const isAdjustment = t.kind === 'adjustment'
  const from = t.fromAccountId ? acctMap[t.fromAccountId] : null
  const to = t.toAccountId ? acctMap[t.toAccountId] : null
  const payeeFromName = from ? from.name : null
  const payeeToName = to ? to.name : null
  const payeeText = payeeName || t.description || '—'

  const signedAmt = (pos ? 1 : -1) * Math.abs(amount)
  const mentionSub = txnMentionSublabel({
    kind: t.kind,
    payeeName,
    accountName: primaryAcct?.name,
    date: t.date,
    amount: Math.abs(amount),
    currency: ccy,
    signed: signedAmt,
  })

  return {
    amount,
    ccy,
    pos,
    baseVal,
    isForeign,
    baseStr,
    fxText,
    amtTitle,
    nativeMain,
    baseMain,
    primaryAcctId,
    primaryAcct,
    isTransfer,
    isAdjustment,
    isSplit,
    xccyOther,
    payeeText,
    payeeFromName,
    payeeToName,
    mentionSub,
  }
}
