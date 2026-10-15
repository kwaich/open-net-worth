/**
 * EntityRow.tsx — extraction-ready DOM emitter for mentionable + screen-aware rows.
 *
 * Given a typed entity, emits ALL the data-ctx-* attributes that both the Lucius
 * @ picker (entityRefProps) and the screen snapshot (buildCtxFields) need, derived
 * from the descriptor's field schema.  The caller owns all visible markup (cells,
 * cards, etc.) via `children` — EntityRow is a transparent wrapper.
 *
 * Architecture contracts (§7, §14.8 R-EX1):
 *   - Reuses entityRefProps + buildCtxFields + filterByDescriptor VERBATIM.
 *     No parallel attribute-stamping mechanism.
 *   - No ONW-domain imports (@/api, @/state, @/hooks).  Pure lucius/ imports only.
 *   - Right-click / drag / occluder wiring are global/delegated (dragRightClick.tsx);
 *     EntityRow stamps data-entity-id + draggable; the delegation picks it up for free.
 */

import type { ReactNode, MouseEvent, ElementType, CSSProperties, HTMLAttributes, Ref } from 'react'
import { entityRefProps } from '../utils/mentionable'
import { buildCtxFields } from './ctxFields'
import { filterByDescriptor } from './entityRegistry'

// ── Props ─────────────────────────────────────────────────────────────────────

// Extra host-element attributes (className, style, title, aria-*, data-*, key, …)
// flow through `...rest`.  The named props below are destructured out so they are
// never double-applied, and the engine's computed data-* attributes are spread
// AFTER `rest` (see the component) so a stray caller prop can never clobber them.
export interface EntityRowProps<T extends object> extends Omit<
  HTMLAttributes<HTMLElement>,
  'onClick' | 'color'
> {
  /** Descriptor type key — e.g. "holding", "account", "payee". */
  type: string
  /** Resolvable ref: fed into data-entity-id="{type}:{id}". */
  id: string
  /** Short display label for the chip. */
  label: string
  /** View-model item — passed through to toFields. */
  item: T
  /**
   * Maps the view-model to the descriptor's canonical key set.
   * Only keys present in the descriptor schema survive filterByDescriptor;
   * sensitive keys are dropped before reaching the DOM.
   */
  toFields: (item: T) => Record<string, string | number | null | undefined>
  /** Broad search string — anything visible beyond the label (entityRefProps `search`). */
  search?: string
  /** Rich second line for the picker row. */
  sublabel?: string
  /** Account color tint — isSafeCssColor-guarded inside entityRefProps. */
  color?: string
  /** Whether the row is draggable into the Lucius dock. Default: true. */
  draggable?: boolean
  /** Whether the row is part of a multi-selection — stamps data-entity-selected for batch drag. */
  selected?: boolean
  /**
   * Ref to the host element — forwarded so callers (e.g. dnd-kit's
   * sortable.setNodeRef) can attach to the host without converting EntityRow to
   * forwardRef (which would erase the generic <T>).
   */
  hostRef?: Ref<HTMLElement>
  /** Host element: 'tr' for table rows, 'div' for cards, 'li' for lists, 'button' for interactive rows, 'article' for sectioning cards. Default: 'div'. */
  as?: 'tr' | 'div' | 'li' | 'button' | 'article'
  /** Host className — forwarded so EntityRow can wrap real table rows / cards. */
  className?: string
  /** Host inline style — forwarded onto the host element. */
  style?: CSSProperties
  /** Host title (tooltip) — forwarded onto the host element. */
  title?: string
  /**
   * The host element's native `type` attribute (e.g. "button" for `as="button"`).
   * Named separately from the entity `type` prop so the two never collide — the
   * entity type drives data-entity-id/data-ctx-type; `htmlType` is the raw DOM attr.
   */
  htmlType?: string
  /** All visible cell/content markup — EntityRow is a transparent wrapper. */
  children: ReactNode
  onClick?: (e: MouseEvent) => void
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * EntityRow — emits all data-ctx-* attributes for a typed domain row so any
 * screen rendering typed data is mentionable + screen-aware by construction.
 *
 * DO NOT adopt in the 9 existing emitters yet — 1e is a separate UI-gated pass.
 * This component is BUILD ONLY in Phase 1.
 */
export function EntityRow<T extends object>({
  type,
  id,
  label,
  item,
  toFields,
  search,
  sublabel,
  color,
  draggable = true,
  selected,
  hostRef,
  as: Host = 'div',
  className,
  style,
  title,
  htmlType,
  children,
  onClick,
  ...rest
}: EntityRowProps<T>) {
  const filtered = filterByDescriptor(type, toFields(item))
  const ctxFieldsStr = buildCtxFields(filtered)

  const HostEl = Host as ElementType

  // Spread order is load-bearing: caller passthrough (`rest`) FIRST so the
  // engine's identity/context attributes always win.  entityRefProps stamps
  // data-entity-* (data-entity-id, …); the fixed data-ctx-* attrs stamp the
  // screen-context schema.  A stray `data-entity-id` / `data-ctx-fields` in
  // `rest` is therefore overridden, never the other way around.
  return (
    <HostEl
      {...rest}
      {...entityRefProps(type, id, label, search, sublabel, color)}
      data-ctx=""
      data-ctx-type={type}
      data-ctx-fields={ctxFieldsStr}
      className={className}
      style={style}
      title={title}
      type={htmlType}
      draggable={draggable}
      data-entity-selected={selected ? '' : undefined}
      onClick={onClick}
      ref={hostRef}
    >
      {children}
    </HostEl>
  )
}
