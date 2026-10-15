import { useState } from 'react'
import {
  Badge,
  Button,
  Cluster,
  CollapsibleSection,
  ErrorState,
  Panel,
  Stack,
  Text,
} from '../../ui'
import { swapCanonical, forgetMatchDecision } from '../../../api/matches'
import type { MatchReview, MatchTxn } from '../../../api/matches'
import { fDate } from '../../../utils/format'

interface DeduplicationPairRowProps {
  review: MatchReview
  accountNames: Map<string, string>
  onAccept: (id: string) => Promise<{ error: string | null; canonicalDescription: string | null }>
  onDismiss: (id: string) => Promise<string | null>
  onUndo: (absorbed_id: string) => Promise<string | null>
  onReload: () => Promise<void>
}

type RowStatus = 'pending' | 'accepted' | 'dismissed' | 'forgotten'

function resolveAccount(id: string | null, names: Map<string, string>): string {
  if (!id) return ''
  return names.get(id) ?? id
}

// ── F20: Alternative candidate card ──────────────────────────────────────────

interface AltCandidateProps {
  alt: MatchTxn
  reviewId: string
  accountNames: Map<string, string>
  disabled: boolean
  onSwapped: () => void
}

function AltCandidate({ alt, reviewId, accountNames, disabled, onSwapped }: AltCandidateProps) {
  const [swapping, setSwapping] = useState(false)
  const [swapError, setSwapError] = useState<string | null>(null)

  const altAccount = resolveAccount(alt.from_account_id ?? alt.to_account_id, accountNames)
  const altSourceLabel = alt.source ?? alt.kind

  async function handleSwap() {
    setSwapping(true)
    setSwapError(null)
    try {
      await swapCanonical(reviewId, alt.id)
      onSwapped()
    } catch (e) {
      setSwapError(e instanceof Error ? e.message : 'Could not swap canonical.')
      setSwapping(false)
    }
  }

  return (
    <Panel className="dedup-alt-candidate">
      <Cluster gap="--space-3" align="center" justify="between">
        <Stack gap="--space-1">
          <Text size="sm" truncate title={alt.description}>
            {alt.description}
          </Text>
          <Text size="xs" tone="faint">
            {fDate(alt.date, 'numeric')} • {alt.amount} {alt.currency}
          </Text>
          <Text size="xs" tone="faint">
            {[altAccount, altSourceLabel].filter(Boolean).join(' • ')}
          </Text>
        </Stack>
        <Stack gap="--space-1">
          <Button
            variant="secondary"
            size="sm"
            loading={swapping}
            disabled={disabled && !swapping}
            onClick={() => void handleSwap()}
          >
            Use this as existing
          </Button>
          {swapError && (
            <Text tone="bad" size="xs">
              {swapError}
            </Text>
          )}
        </Stack>
      </Cluster>
    </Panel>
  )
}

// ── Main row ──────────────────────────────────────────────────────────────────

export function DeduplicationPairRow({
  review,
  accountNames,
  onAccept,
  onDismiss,
  onUndo,
  onReload,
}: DeduplicationPairRowProps) {
  const [rowStatus, setRowStatus] = useState<RowStatus>('pending')
  const [accepting, setAccepting] = useState(false)
  const [dismissing, setDismissing] = useState(false)
  const [undoing, setUndoing] = useState(false)
  const [forgetting, setForgetting] = useState(false)
  const [forgetError, setForgetError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [altOpen, setAltOpen] = useState(false)
  const [mergedIntoDescription, setMergedIntoDescription] = useState<string | null>(null)

  // F17: missing-row guard — render if either leg is null
  if (!review.incoming || !review.canonical) {
    return (
      <Panel className="dedup-pair-row">
        <Stack gap="--space-3">
          <Cluster gap="--space-2" align="center">
            <Badge tone="muted">Already resolved</Badge>
          </Cluster>
          <ErrorState
            title="This review can no longer be actioned."
            message="One or both transactions were deleted or merged elsewhere. Dismiss this item to clear it."
          />
          {rowStatus === 'dismissed' ? (
            <Cluster gap="--space-2" align="center">
              <Text size="xs" tone="muted">
                Dismissed — remembered as different.
              </Text>
              <Button
                variant="ghost"
                size="sm"
                loading={undoing}
                onClick={() => {
                  // For missing-row there is no absorbed_id to undo with;
                  // undo just reloads which is acceptable for the edge case.
                  void (async () => {
                    setUndoing(true)
                    setError(null)
                    await onReload()
                    setUndoing(false)
                    setRowStatus('pending')
                  })()
                }}
              >
                Undo
              </Button>
            </Cluster>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              loading={dismissing}
              onClick={() => {
                void (async () => {
                  setDismissing(true)
                  setError(null)
                  const err = await onDismiss(review.id)
                  setDismissing(false)
                  if (err) {
                    setError(err)
                  } else {
                    setRowStatus('dismissed')
                  }
                })()
              }}
            >
              Dismiss
            </Button>
          )}
          {error && (
            <Text tone="bad" size="xs">
              {error}
            </Text>
          )}
        </Stack>
      </Panel>
    )
  }

  // Both legs present — normal flow below
  const incoming = review.incoming
  const canonical = review.canonical

  async function handleAccept() {
    setAccepting(true)
    setError(null)
    const { error: err, canonicalDescription } = await onAccept(review.id)
    setAccepting(false)
    if (err) {
      setError(err)
    } else {
      setMergedIntoDescription(canonicalDescription)
      setRowStatus('accepted')
    }
  }

  async function handleDismiss() {
    setDismissing(true)
    setError(null)
    const err = await onDismiss(review.id)
    setDismissing(false)
    if (err) {
      setError(err)
    } else {
      setRowStatus('dismissed')
    }
  }

  async function handleUndo() {
    setUndoing(true)
    setError(null)
    const err = await onUndo(incoming.id)
    setUndoing(false)
    if (err) {
      setError(err)
    } else {
      setRowStatus('pending')
    }
  }

  const incomingAccount = resolveAccount(
    incoming.from_account_id ?? incoming.to_account_id,
    accountNames,
  )
  const canonicalAccount = resolveAccount(
    canonical.from_account_id ?? canonical.to_account_id,
    accountNames,
  )
  const incomingSourceLabel = incoming.source ?? incoming.kind
  const canonicalSourceLabel = canonical.source ?? canonical.kind
  const reasonText = review.llm_reason ?? review.reasons.join('. ')
  const hasAlts = (review.alternatives?.length ?? 0) > 0
  const altCount = review.alternatives?.length ?? 0
  // Accept/Dismiss blocked while alternatives are open
  const actionsDisabled = altOpen

  return (
    <Panel className="dedup-pair-row">
      <Stack gap="--space-3">
        {/* Top row: badges + actions */}
        <Cluster justify="between" align="start" gap="--space-3">
          <Cluster gap="--space-2" align="center">
            {review.klass === 'near_duplicate' ? (
              <Badge tone="warn">Near duplicate</Badge>
            ) : (
              <Badge tone="neutral">Transfer pair</Badge>
            )}
            <Badge tone="muted">{Math.round(review.score * 100)}% match</Badge>
            {hasAlts && <Badge tone="warn">Ambiguous match</Badge>}
            {review.become_transfer && <Badge tone="pos">Will become transfer</Badge>}
          </Cluster>

          {rowStatus === 'pending' && (
            <Cluster gap="--space-2" align="center">
              <Button
                variant="primary"
                size="sm"
                loading={accepting}
                disabled={actionsDisabled}
                onClick={() => void handleAccept()}
                aria-label={`Accept: ${incoming.description} / ${canonical.description}`}
              >
                Accept
              </Button>
              <Button
                variant="ghost"
                size="sm"
                loading={dismissing}
                disabled={actionsDisabled}
                onClick={() => void handleDismiss()}
                aria-label={`Dismiss: ${incoming.description} / ${canonical.description}`}
              >
                Dismiss
              </Button>
            </Cluster>
          )}

          {rowStatus === 'accepted' && (
            <Cluster gap="--space-2" align="center">
              <Text size="xs" tone="good">
                Merged into {mergedIntoDescription || 'the existing transaction'}.
              </Text>
              <Button variant="ghost" size="sm" loading={undoing} onClick={() => void handleUndo()}>
                Undo
              </Button>
            </Cluster>
          )}

          {rowStatus === 'dismissed' && (
            <Cluster gap="--space-2" align="center">
              <Text size="xs" tone="muted">
                Dismissed — remembered as different.
              </Text>
              <Button variant="ghost" size="sm" loading={undoing} onClick={() => void handleUndo()}>
                Undo
              </Button>
              <Button
                variant="ghost"
                size="sm"
                loading={forgetting}
                onClick={() => {
                  void (async () => {
                    setForgetting(true)
                    setForgetError(null)
                    try {
                      await forgetMatchDecision(review.signature)
                      setRowStatus('forgotten')
                    } catch (e) {
                      setForgetError(e instanceof Error ? e.message : 'Could not forget decision.')
                    } finally {
                      setForgetting(false)
                    }
                  })()
                }}
              >
                Forget
              </Button>
              {forgetError && (
                <Text tone="bad" size="xs">
                  {forgetError}
                </Text>
              )}
            </Cluster>
          )}

          {rowStatus === 'forgotten' && (
            <Text size="xs" tone="faint">
              Dismissed. Decision cleared — the engine will re-evaluate this pair if seen again.
            </Text>
          )}
        </Cluster>

        {/* Comparison grid */}
        <div className="dedup-pair-grid">
          {/* Incoming */}
          <Stack gap="--space-2">
            <Text size="xs" tone="muted" weight={600}>
              Incoming
            </Text>
            <Text size="sm" tone="faint">
              {fDate(incoming.date, 'numeric')}
            </Text>
            <Text size="sm" truncate title={incoming.description}>
              {incoming.description}
            </Text>
            <Text size="md" weight={600}>
              {incoming.amount} {incoming.currency}
            </Text>
            <Text size="xs" tone="faint">
              {[incomingAccount, incomingSourceLabel].filter(Boolean).join(' • ')}
            </Text>
          </Stack>

          {/* Existing / canonical */}
          <Stack gap="--space-2">
            <Text size="xs" tone="muted" weight={600}>
              Existing
            </Text>
            <Text size="sm" tone="faint">
              {fDate(canonical.date, 'numeric')}
            </Text>
            <Text size="sm" truncate title={canonical.description}>
              {canonical.description}
            </Text>
            <Text size="md" weight={600}>
              {canonical.amount} {canonical.currency}
            </Text>
            <Text size="xs" tone="faint">
              {[canonicalAccount, canonicalSourceLabel].filter(Boolean).join(' • ')}
            </Text>
          </Stack>
        </div>

        {/* Reason */}
        {reasonText && (
          <Text size="xs" tone="muted">
            {reasonText}
          </Text>
        )}

        {/* UX-4: tie hint — shown when alternatives exist but section is collapsed */}
        {hasAlts && !altOpen && (
          <Text size="xs" tone="warn" as="p">
            Two candidates matched equally — expand below to choose before accepting.
          </Text>
        )}

        {/* F20: alternatives disclosure */}
        {hasAlts && (
          <CollapsibleSection
            title={`${altCount} alternative match${altCount > 1 ? 'es' : ''} at the same score`}
            defaultOpen={false}
            open={altOpen}
            onOpenChange={setAltOpen}
          >
            <Stack gap="--space-3">
              <Text as="p" size="xs" tone="warn">
                A second transaction matched with equal confidence. Choose which existing
                transaction is the duplicate before accepting.
              </Text>
              {review.alternatives!.map((alt) => (
                <AltCandidate
                  key={alt.id}
                  alt={alt}
                  reviewId={review.id}
                  accountNames={accountNames}
                  disabled={accepting || dismissing}
                  onSwapped={() => {
                    setAltOpen(false)
                    void onReload()
                  }}
                />
              ))}
            </Stack>
          </CollapsibleSection>
        )}

        {/* Inline error */}
        {error && (
          <Text tone="bad" size="xs">
            {error}
          </Text>
        )}
      </Stack>
    </Panel>
  )
}
