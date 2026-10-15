import { Info, Pencil, Wallet } from '../../../ui/icons'
import { sourceLine, type ReviewDraft } from './transactionReview'
import { SourceIcon } from './pickers'
import { ReviewChoices, ReviewSummary } from './ReviewSummary'
import type { Account, Category, Txn } from './types'

/**
 * The rear card: a preview of the NEXT transaction, drawn on the same surface AND with the
 * same body as the front card — a swipe reveals a real card, not a reduced one. Hidden from
 * assistive tech (the front card is the one under review) and inert to input via CSS, so its
 * chips and the Edit affordance are decoration; the Edit is a span, never a second button.
 */
export function TransactionReviewPeek({
  txn,
  draft,
  account,
  accounts,
  categories,
  suggestedCategoryIds,
}: {
  txn: Txn
  draft: ReviewDraft
  account: Account | null
  accounts: Account[]
  categories: Category[]
  suggestedCategoryIds: string[]
}) {
  return (
    <div
      className="acct-review-card acct-review-card--rear"
      aria-hidden="true"
      // Focusable children (the category chips) stay out of the tab order — aria-hidden alone
      // hides them from the a11y tree but leaves them tabbable.
      inert
      data-testid="transaction-review-card-rear"
    >
      <div className="acct-review-facts" data-kind={draft.kind}>
        <div className="acct-review-card-head">
          <b>
            <Wallet size={16} aria-hidden="true" />
            {account?.name ?? 'Unknown account'}
          </b>
          {/* The SAME classes ui/Button composes for the front card's actions (ui-btn + ghost +
              sm), so the two heads are pixel-identical. Spans, so there is never a second
              button. */}
          <span className="acct-review-head-actions">
            <span className="ui-btn ui-btn--ghost ui-btn--sm acct-review-edit-all">
              <Info size={14} aria-hidden="true" />
              Details
            </span>
            <span className="ui-btn ui-btn--ghost ui-btn--sm acct-review-edit-all">
              <Pencil size={14} aria-hidden="true" />
              Edit
            </span>
          </span>
          <span className="acct-review-source">
            <SourceIcon source={txn.source} variant="mono" />
            <span className="acct-review-source-text">{sourceLine(txn)}</span>
          </span>
        </div>
        <ReviewSummary txn={txn} accounts={accounts} draft={draft} />
      </div>
      <ReviewChoices
        txn={txn}
        accounts={accounts}
        categories={categories}
        draft={draft}
        onDraftChange={() => {}}
        suggestedCategoryIds={suggestedCategoryIds}
      />
    </div>
  )
}
