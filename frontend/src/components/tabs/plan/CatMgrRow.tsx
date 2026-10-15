// plan/CatMgrRow.tsx — the desktop "Manage categories" grid row (ported from prototype/plan/
// envelopes.jsx CategoriesManager/CatMgrRow/EnvelopeCell — DOM/class/copy verbatim), split out
// of categoriesManager.tsx so its mobile card twin (CatMgrCard.tsx) can import the shared
// pieces (ManagedCategory/EnvelopeRow/KIND_LABEL/kindPillClass/EnvelopeCell/
// CatMgrRowProps) without a categoriesManager.tsx <-> CatMgrCard.tsx import cycle — mirrors
// how accounts/TxnRow.tsx and accounts/TxnCard.tsx are split (TxnCard imports from TxnRow,
// TxnRow never imports from Register).
import { ColorDot, ColorIcon, InlineEdit, Kebab } from './budgetParts'
import { STRUCTURAL_CATEGORY_KEYS } from '../../../hooks/useCategories'
import { EntityRow } from '../../../lucius/EntityRow'

// The raw store row shape (backend/db/category_store.py). `id` is an
// opaque, immutable id (`cat_<12hex>` — see docs/adr/2026-07-05-category-opaque-ids.md).
export interface ManagedCategory {
  id: string
  label: string
  kind: string
  color: string
  hidden?: boolean
}
export interface EnvelopeRow {
  id: string
  name: string
  color?: string
  category_ids: string[]
}

// User-facing kind labels — "Inflow" reads better than the raw "income" everywhere a kind
// name is shown to the user (filter chips, the row's Kind pill, reassign-target groups); the
// wire value stays "income" (categories.py's `_VALID_KINDS`). `kindPillClass` bridges the real
// kind to the prototype's own CSS scheme (`.pl-kindpill.inflow/.transfer/.investment` — plain
// expense gets no colour modifier).
export const KIND_LABEL: Record<string, string> = {
  expense: 'Expense',
  income: 'Inflow',
  transfer: 'Transfer',
  investment: 'Investment',
}
export function kindPillClass(kind: string): string {
  // Every kind gets its colour (owner, 2026-07-04): expense red, inflow green, transfer blue,
  // investment purple; unknown legacy kinds (e.g. a grandfathered 'adjustment' row) stay plain.
  return kind === 'income'
    ? 'inflow'
    : kind === 'transfer'
      ? 'transfer'
      : kind === 'investment'
        ? 'investment'
        : kind === 'expense'
          ? 'expense'
          : ''
}

export function EnvelopeCell({
  envelope,
  labelOf,
}: {
  envelope: EnvelopeRow | undefined
  labelOf: (key: string) => string
}) {
  if (!envelope)
    return (
      <span
        className="pl-catmgr-env pl-catmgr-env--none"
        title="Link it to an envelope on the Budget table to budget for it"
      >
        Not budgeted
      </span>
    )
  return (
    // CONF-6 — the tooltip resolves each member key to its LABEL (prototype envelopes.jsx:375),
    // not the raw store key ("food_dining, groceries_x1" reads as machine internals).
    <span className="pl-catmgr-env" title={envelope.category_ids.map(labelOf).join(', ')}>
      {envelope.name}
      {envelope.category_ids.length > 1 && (
        <span className="pl-gcount">{envelope.category_ids.length}</span>
      )}
    </span>
  )
}

// Shared prop shape for both the desktop grid row and its mobile card twin (CatMgrCard.tsx) —
// same handlers, same data, so the parent picks the component by useIsMobile() without any
// prop-shape branching (categoriesManager.tsx's rows.map).
export interface CatMgrRowProps {
  c: ManagedCategory
  envelope: EnvelopeRow | undefined
  labelOf: (key: string) => string
  renaming: boolean
  onStartRename: () => void
  onCancelRename: () => void
  onRename: (label: string) => void
  onRecolor: (color: string) => void
  onToggleHide: () => void
  onDelete: () => void
  /** ENV-RES-06 — undefined for a category that's kind:income (envelopes are spending-only)
   *  or already linked; present (a callback) for any other unlinked category. */
  onAddToEnvelope?: () => void
}

export function CatMgrRow({
  c,
  envelope,
  labelOf,
  renaming,
  onStartRename,
  onCancelRename,
  onRename,
  onRecolor,
  onToggleHide,
  onDelete,
  onAddToEnvelope,
}: CatMgrRowProps) {
  const locked = STRUCTURAL_CATEGORY_KEYS.has(c.id)
  return (
    // Lucius mention source — THIS is the category-identity surface (budget rows emit
    // envelope refs, ADR 2026-07-11): drag-into-dock + right-click "Ask Lucius" via the
    // global delegated handlers. Drag pauses while renaming so the InlineEdit's text
    // selection isn't hijacked by a row drag.
    <EntityRow
      type="category"
      id={c.id}
      label={c.label}
      color={c.color}
      item={c}
      toFields={(it) => ({
        label: it.label,
        kind: it.kind,
        budgeted: envelope ? envelope.name : 'Not budgeted',
      })}
      className={'pl-trow pl-crow pl-catmgr-row' + (c.hidden ? ' cat-hidden' : '')}
      draggable={!renaming}
    >
      <span className="pl-cname">
        {locked ? (
          <span className="pl-cdot-wrap">
            <span className="pl-cdot--btn" aria-hidden="true" style={{ cursor: 'default' }}>
              <ColorIcon icon="tag" color={c.color} />
            </span>
          </span>
        ) : (
          <ColorDot color={c.color} onPick={onRecolor} label={'Color for ' + c.label} icon="tag" />
        )}
        <span className="pl-cmain">
          {renaming ? (
            <InlineEdit initial={c.label} onSave={onRename} onCancel={onCancelRename} />
          ) : (
            <span
              className="pl-clabel"
              title={locked ? undefined : 'Double-click to rename'}
              onDoubleClick={() => {
                if (!locked) onStartRename()
              }}
            >
              {c.label}
            </span>
          )}
        </span>
        {locked ? (
          <span
            className="pl-kebab-wrap"
            aria-hidden="true"
            style={{ visibility: 'hidden', pointerEvents: 'none' }}
          >
            <button className="pl-kebab" tabIndex={-1} />
          </span>
        ) : (
          <Kebab
            label={c.label + ' options'}
            items={[
              { label: 'Rename', onClick: onStartRename },
              // ENV-RES-06 — a dormant unlinked category (no activity this month) never shows
              // up under Outside envelopes' Unbudgeted section, so the manager needs its OWN
              // re-link path; leads right after Rename, mirroring the empty-envelope row's own
              // "Add categories…" precedence in the Budget table's kebab (budgetEnvelopeOps.tsx).
              ...(onAddToEnvelope ? [{ label: 'Add to envelope…', onClick: onAddToEnvelope }] : []),
              { label: c.hidden ? 'Unhide' : 'Hide', onClick: onToggleHide },
              { divider: true },
              { label: 'Delete…', danger: true, onClick: onDelete },
            ]}
          />
        )}
      </span>
      <span>
        <span className={'pl-kindpill ' + kindPillClass(c.kind)}>
          {KIND_LABEL[c.kind] ?? c.kind}
        </span>
      </span>
      <span>
        <EnvelopeCell envelope={envelope} labelOf={labelOf} />
      </span>
      <span className="pl-catmgr-hide">
        {/* Prototype-exact bare toggle (envelopes.jsx CatMgrRow) — the ui/ Switch renders its
            label VISIBLY, which wrapped into a 4-line "Hide Food & Dining" column. */}
        <button
          type="button"
          role="switch"
          aria-checked={!!c.hidden}
          disabled={locked}
          className={'pl-tgt-switch' + (c.hidden ? ' on' : '')}
          title={
            locked
              ? 'A structural category cannot be hidden'
              : (c.hidden ? 'Unhide ' : 'Hide ') + c.label
          }
          aria-label={(c.hidden ? 'Unhide ' : 'Hide ') + c.label}
          onClick={() => {
            if (!locked) onToggleHide()
          }}
        >
          <span />
        </button>
      </span>
    </EntityRow>
  )
}
