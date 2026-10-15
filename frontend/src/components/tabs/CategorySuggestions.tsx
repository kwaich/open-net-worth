import { useEffect, useState } from 'react'
import { authFetch } from '../../api/client'
import { CollapsibleSection, Button, Badge, EmptyState, Loading } from '../ui'
import { fPct } from '../../utils/format'

// Learned category suggestions (competitor gap #1, L1). For each UNCATEGORIZED
// expense/income transaction, the backend (services/categorize.py) suggests a
// category drawn from how the user has categorized that same payee before, with a
// confidence score — the unruled long-tail complement to the rule suggestions above.
//
// This is a READ surface + APPLY via the EXISTING write path: applying a group reuses
// PATCH /api/transactions/bulk (= bulk_update_transactions, set category on an id list)
// — there is NO new write endpoint. UI↔Lucius parity with the get_category_suggestions
// tool. A soft surface: any error → empty (never blocks the Rules page).

interface CategorySuggestion {
  transaction_id: string
  payee_key: string
  description: string
  suggested_category: string
  confidence: number
  evidence_count: number
  kind: string
}

interface CategoryDef {
  id: string
  label: string
}

interface Props {
  categories: CategoryDef[]
}

export function CategorySuggestions({ categories }: Props) {
  const [suggestions, setSuggestions] = useState<CategorySuggestion[]>([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  // The suggested_category currently being applied (one apply in flight).
  const [busy, setBusy] = useState<string | null>(null)

  const labelFor = (key: string) => categories.find((c) => c.id === key)?.label || 'Uncategorized'

  function load() {
    setLoading(true)
    authFetch('/api/category-suggestions')
      .then((r) => (r.ok ? r.json() : { suggestions: [] }))
      .then((data: { suggestions?: CategorySuggestion[] }) => {
        setSuggestions(Array.isArray(data.suggestions) ? data.suggestions : [])
      })
      .catch(() => setSuggestions([]))
      .finally(() => {
        setLoading(false)
        setLoaded(true)
      })
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Group by the suggested category so the user can apply a whole category at once
  // (one bulk_update_transactions call). Sorted by group size desc.
  const groups = Object.values(
    suggestions.reduce<Record<string, { category: string; rows: CategorySuggestion[] }>>(
      (acc, s) => {
        const g = (acc[s.suggested_category] ||= { category: s.suggested_category, rows: [] })
        g.rows.push(s)
        return acc
      },
      {},
    ),
  ).sort((a, b) => b.rows.length - a.rows.length)

  async function applyGroup(category: string, ids: string[]) {
    setBusy(category)
    try {
      const resp = await authFetch('/api/transactions/bulk', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids, changes: { category } }),
      })
      if (!resp.ok) throw new Error(await resp.text())
      // Re-read so the just-applied rows drop out of the list.
      load()
    } catch {
      // Soft failure: leave the list as-is; a reload will reconcile.
    } finally {
      setBusy(null)
    }
  }

  // Hide the whole section until we know there's something to show (no empty noise on
  // a fresh book — the Rules suggestions above already cover the empty case).
  if (loaded && suggestions.length === 0) return null

  const count = suggestions.length

  return (
    <CollapsibleSection
      title="Suggested categories"
      defaultOpen
      actions={count > 0 ? <Badge tone="neutral">{count}</Badge> : undefined}
    >
      {loading && !loaded ? (
        <Loading />
      ) : groups.length === 0 ? (
        <EmptyState
          title="No category suggestions"
          body="Uncategorized transactions get a suggestion once you've categorized that payee before."
        />
      ) : (
        <div className="cat-suggest">
          <p className="cat-suggest__hint">
            Categories learned from how you've labeled each payee before. Apply a whole group, then
            refine on the Accounts register.
          </p>
          {groups.map((g) => (
            <div key={g.category} className="cat-suggest__group">
              <div className="cat-suggest__group-head">
                <span className="cat-suggest__cat">{labelFor(g.category)}</span>
                <Badge tone="muted">{g.rows.length}</Badge>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busy === g.category}
                  onClick={() =>
                    applyGroup(
                      g.category,
                      g.rows.map((r) => r.transaction_id),
                    )
                  }
                >
                  {busy === g.category ? 'Applying…' : `Apply to ${g.rows.length}`}
                </Button>
              </div>
              <ul className="cat-suggest__rows">
                {g.rows.slice(0, 6).map((r) => (
                  <li key={r.transaction_id} className="cat-suggest__row">
                    <span className="cat-suggest__desc">{r.description}</span>
                    <span className="cat-suggest__conf">
                      {fPct(r.confidence, { fraction: true })}
                    </span>
                  </li>
                ))}
                {g.rows.length > 6 && (
                  <li className="cat-suggest__more">+{g.rows.length - 6} more</li>
                )}
              </ul>
            </div>
          ))}
        </div>
      )}
    </CollapsibleSection>
  )
}
