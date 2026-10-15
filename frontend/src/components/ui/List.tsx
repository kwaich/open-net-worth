import type { ReactNode } from 'react'

export interface ListProps {
  /** Numbered (`<ol>`) instead of bulleted (`<ul>`). */
  ordered?: boolean
  children: ReactNode
}

/** List — a token-spaced bulleted or numbered list (steps, short enumerations). Items are plain
 *  `<li>`; inline emphasis is `<strong>`, which inherits the list's size. */
export function List({ ordered = false, children }: ListProps) {
  const Tag = ordered ? 'ol' : 'ul'
  return <Tag className="ui-list">{children}</Tag>
}
