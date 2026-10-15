// mentionRow.ts — core MentionRow type declaration.
//
// Lives in lucius/ (core, no ONW-domain imports) so matchEntity.ts can import it
// without creating a core→hook cycle (matchEntity.ts was previously importing from
// hooks/useMentionables.ts — F3 in the RFC §14.0 gap analysis).
//
// The hook (useMentionables.ts) re-exports this type for backward compat so existing
// callers that import MentionRow from the hook do not need to be updated.

export interface MentionRow {
  type: string
  id: string
  label: string
  sublabel: string
  value: string
  icon: string
  surface?: string
  /** True for DOM-harvested and recently-picked rows — renders under "On this screen". */
  onScreen?: boolean
  /** Broad search string from data-entity-search — anything visible on the row beyond the label
   * (e.g. full instrument name + account). Local-only; never set by backend rows. */
  search?: string
  /** Why-hint from matchEntity: set when the row matched by type-synonym or amount/category
   * rather than by name. Rendered as the row's sub-line in the picker. */
  hint?: string
  /** The entity's own display color (account tint, envelope/category dot) — from
   * data-entity-color on harvested rows or the backend row's optional `color`.
   * Used to tint the picker icon + chip glyph to match the on-screen color dot. */
  color?: string
}
