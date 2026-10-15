// plan/CatMgrCard.tsx — mobile twin of CatMgrRow (CatMgrRow.tsx), the same role TxnCard plays
// for TxnRow (accounts/TxnCard.tsx, Register.tsx:153): the desktop `.pl-catmgr-modal` grid (4
// fixed-width columns, `.pl-table-inner { min-width: 560px }`) forces horizontal scroll below
// ~640px — CategoriesManager swaps CatMgrRow for CatMgrCard per row there instead, stacking the
// same fields into 2 rows and dropping the grid header. Same handlers, same data
// (CatMgrRowProps) — categoriesManager.tsx picks the component by useIsMobile(), no prop-shape
// branching.
//
// Imports from CatMgrRow.tsx, never from categoriesManager.tsx: categoriesManager.tsx itself
// imports THIS file (to pick CatMgrRow vs CatMgrCard per useIsMobile()), so importing back from
// it would be a circular import (confirmed via `npx madge --circular`) — CatMgrRow.tsx is the
// shared leaf both sides depend on instead.
import { ColorDot, ColorIcon, InlineEdit, Kebab } from './budgetParts'
import { STRUCTURAL_CATEGORY_KEYS } from '../../../hooks/useCategories'
import { EntityRow } from '../../../lucius/EntityRow'
import { EnvelopeCell, KIND_LABEL, kindPillClass, type CatMgrRowProps } from './CatMgrRow'

export function CatMgrCard({
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
    <EntityRow
      as="article"
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
      className={'pl-catmgr-card' + (c.hidden ? ' cat-hidden' : '')}
      draggable={!renaming}
    >
      {/* Row 1 (identity) — dot + label left, Kebab pinned right via .pl-kebab-wrap's own
          margin-left:auto, same DOM shape as CatMgrRow's own .pl-cname. */}
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
              title={locked ? undefined : 'Double-tap to rename'}
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
              ...(onAddToEnvelope ? [{ label: 'Add to envelope…', onClick: onAddToEnvelope }] : []),
              { label: c.hidden ? 'Unhide' : 'Hide', onClick: onToggleHide },
              { divider: true },
              { label: 'Delete…', danger: true, onClick: onDelete },
            ]}
          />
        )}
      </span>
      {/* Row 2 (classification) — kind pill left, envelope badge/"Not budgeted" right.
          The standalone "Hidden" toggle that used to sit in its own row 3
          was removed (2026-09): hide/unhide is fully wired through the kebab's Hide/Unhide
          item above, so a second control for the same action was redundant. */}
      <span className="pl-catmgr-card-row">
        <span className={'pl-kindpill ' + kindPillClass(c.kind)}>
          {KIND_LABEL[c.kind] ?? c.kind}
        </span>
        <EnvelopeCell envelope={envelope} labelOf={labelOf} />
      </span>
    </EntityRow>
  )
}
