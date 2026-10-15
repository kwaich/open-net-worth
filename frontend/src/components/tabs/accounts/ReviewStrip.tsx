/**
 * ReviewStrip.tsx — "N to review · Start review · Mark all reviewed", the line under the balances
 * on the Accounts header and the All-accounts overview.
 *
 * Review is a flag, not a balance: an import, Lucius or an automation added the transaction and
 * you have not confirmed it yet. It changes no figure, so clearing it for every row is a safe
 * bulk action, offered here beside walking through them one at a time.
 */

import { InfoPopover } from '../../ui'
import { WalletCards } from '../../../ui/icons'

const REVIEW_EXPLAINER =
  "Review means an import, Lucius or an automation added this and you haven't confirmed it yet. " +
  'It changes no balance; approving only clears the flag.'

export function ReviewStrip({
  count,
  onReview,
  onStartReview,
  onMarkAllReviewed,
}: {
  count: number
  onReview: () => void
  onStartReview: () => void
  /** Clears the flag on every row waiting for it (the caller confirms first). */
  onMarkAllReviewed?: () => void
}) {
  if (count <= 0) return null
  return (
    <span className="acct-recon-review">
      <button
        type="button"
        className="acct-recon-count as-btn"
        onClick={onReview}
        title="Show transactions that need review"
      >
        {count} to review
      </button>
      <InfoPopover width={260} text={REVIEW_EXPLAINER} />
      <button
        type="button"
        className="acct-recon-start as-btn"
        onClick={onStartReview}
        title="Approve or skip each one in turn"
      >
        <WalletCards size={13} aria-hidden="true" /> Start review
      </button>
      {onMarkAllReviewed && (
        <button
          type="button"
          className="acct-recon-start as-btn"
          onClick={onMarkAllReviewed}
          title="Clear the Review flag on all of them at once"
        >
          Mark all reviewed
        </button>
      )}
    </span>
  )
}
