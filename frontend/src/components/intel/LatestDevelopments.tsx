import { newsTime, safeHref } from '../../utils/format'
import type { NewsItem } from '../../types/assetDetail'
import { useIsMobile } from '../../hooks/useIsMobile'
import { Favicon } from './Favicon'

interface Props {
  items: NewsItem[]
  // Items already shown in the grid (first 4) are excluded here
  offset?: number
  // Max rows to show before "View all" (undefined = show all). Toggled via onToggle/expanded.
  limit?: number
  /** A tighter cap below the phone breakpoint, where the list is one column and ten rows run
   *  a whole screen (owner ask 2026-09-22: "on mobile, when it turns one column, max 5"). */
  phoneLimit?: number
  expanded?: boolean
  onToggle?: () => void
}

export function LatestDevelopments({
  items,
  offset = 4,
  limit,
  phoneLimit,
  expanded = false,
  onToggle,
}: Props) {
  const isMobile = useIsMobile()
  const cap = isMobile && phoneLimit != null ? phoneLimit : limit
  const rest = items.slice(offset)
  if (!rest.length) return null

  const shown = cap != null && !expanded ? rest.slice(0, cap) : rest
  const canToggle = cap != null && rest.length > cap

  return (
    <div>
      <div className="asd-section-head">
        <span className="asd-section-title">Latest Developments</span>
        {canToggle && onToggle && (
          <button type="button" className="asd-view-all-btn" onClick={onToggle}>
            {expanded ? 'Show less' : `View all (${rest.length})`}
          </button>
        )}
      </div>
      <div className="asd-dev-list">
        {shown.map((item, i) => {
          const href = safeHref(item.url)
          const Tag = href ? 'a' : 'div'
          const linkProps = href
            ? { href, target: '_blank' as const, rel: 'noopener noreferrer' }
            : {}
          return (
            <Tag key={i} {...linkProps} className="asd-dev-row">
              <div className="asd-dev-meta">
                <Favicon url={item.url} domain={item.domain} className="asd-dev-favicon" />
                <span className="asd-dev-source">{item.source}</span>
                <span className="asd-dev-dot" aria-hidden="true">
                  •
                </span>
                <span className="asd-dev-time">{newsTime(item.published_at)}</span>
              </div>
              <p className="asd-dev-title">{item.title}</p>
            </Tag>
          )
        })}
      </div>
    </div>
  )
}
