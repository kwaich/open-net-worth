import type { CSSProperties, ReactNode } from 'react'

export type TextSize = '2xs' | 'xs' | 'sm' | 'base' | 'md' | 'lg' | 'xl' | '2xl' // ↔ --text-*
export type TextTone = 'ink' | 'muted' | 'faint' | 'good' | 'bad' | 'warn'
export type TextWeight = 400 | 500 | 600 | 700
export type TextElement = 'span' | 'p' | 'div' | 'label' | 'h1' | 'h2' | 'h3' | 'h4'

export interface TextProps {
  as?: TextElement
  /** Maps to `--text-<size>`; default 'base' (13px). */
  size?: TextSize
  weight?: TextWeight
  /** Maps to a color token (`--ink`/`--muted`/`--faint`/`--good`/`--bad`/`--warn`). */
  tone?: TextTone
  /** Single-line ellipsis (sets `min-width:0` + overflow). Pair with `title`. */
  truncate?: boolean
  title?: string
  htmlFor?: string
  className?: string
  style?: CSSProperties
  children: ReactNode
}

/**
 * Text — the typography primitive (RFC §3). The ONE place a size string maps to
 * a `--text-*` token and a tone maps to a color token, so NEW code never writes
 * a raw `font-size`. Headings are `<Text as="h1" size="2xl">` etc. Existing
 * inline font-sizes are migrated per screen (parked), not here.
 *
 * `truncate` opts into single-line ellipsis — always pass a `title` with the
 * full value so the hidden text stays recoverable.
 */
export function Text({
  as: Tag = 'span',
  size = 'base',
  weight,
  tone,
  truncate = false,
  title,
  htmlFor,
  className,
  style: styleProp,
  children,
}: TextProps) {
  const classes = [
    'ui-text',
    `ui-text--${size}`,
    tone ? `ui-text--${tone}` : '',
    truncate ? 'ui-truncate' : '',
    className || '',
  ]
    .filter(Boolean)
    .join(' ')
  const style: CSSProperties | undefined =
    weight || styleProp ? { ...(weight ? { fontWeight: weight } : {}), ...styleProp } : undefined
  return (
    <Tag className={classes} style={style} title={title} htmlFor={htmlFor}>
      {children}
    </Tag>
  )
}
