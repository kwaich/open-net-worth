// Domain types for the cross-tab Portfolio Brief signal aggregator (Wave 2, RFC 09-cross-tab).
// Consumed by useInvestmentSignals → PortfolioBrief.

export type SignalKind = 'concentration' | 'rebalance' | 'vesting' | 'income' | 'today' | 'ontrack'

export type SignalTone = 'warn' | 'accent' | 'good' | 'bad' | 'muted'

export interface InvestmentSignal {
  id: string
  kind: SignalKind
  tone: SignalTone
  /** True when the signal counts toward the "N to decide" pill. */
  actionable: boolean
  /** Short uppercase label rendered as the badge tag (e.g. "Concentration"). */
  tag: string
  /** One-sentence narrative for the lead signal. */
  narrative: string
  /** Optional monetary figure (base currency) for secondary context. */
  amount?: number
}
