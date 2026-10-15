/**
 * EntityList.tsx — optional grouping wrapper for a list of EntityRow elements.
 *
 * Emits `data-ctx-region` + a `data-ctx-fact` count so the screen snapshot
 * includes an entity-count hint ("12 holdings") alongside the rows.
 *
 * Architecture (§7, §14.8 R-EX1):
 *   - No ONW-domain imports.  Pure lucius/ imports only.
 *   - Most migrations need only EntityRow; EntityList is optional for the
 *     region-grouping and count hint.  The group `title` also becomes the
 *     region label in the Lucius screen snapshot.
 */

import type { ReactNode, ElementType } from 'react'

// ── Props ─────────────────────────────────────────────────────────────────────

export interface EntityListProps {
  /**
   * Human-readable region title — emitted as data-ctx-title and as the
   * heading in the snapshot region block (e.g. "Holdings", "Payees").
   */
  title: string
  /**
   * Number of items in the list.  Emitted as a data-ctx-fact so the snapshot
   * includes a count hint ("Holdings: 12 items").
   */
  count: number
  /** Wrapper element. 'div' for cards/panels; 'tbody' for table bodies; 'ul' for lists. */
  as?: 'div' | 'tbody' | 'ul' | 'ol'
  className?: string
  children: ReactNode
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * EntityList — wraps a group of EntityRow elements and stamps the region
 * contract so the screen snapshot groups them under a named section with a
 * count hint.
 *
 * Usage:
 *   <EntityList title="Holdings" count={rows.length} as="tbody">
 *     {rows.map(r => <EntityRow type="holding" ... />)}
 *   </EntityList>
 *
 * DO NOT adopt in the 9 existing emitters yet — 1e is a separate UI-gated pass.
 */
export function EntityList({
  title,
  count,
  as: Host = 'div',
  className,
  children,
}: EntityListProps) {
  const HostEl = Host as ElementType

  return (
    <HostEl
      data-ctx-region="content"
      data-ctx-title={title}
      data-ctx-fact={`${title}: ${count} items`}
      className={className}
    >
      {children}
    </HostEl>
  )
}
