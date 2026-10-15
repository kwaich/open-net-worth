import { useState } from 'react'
import { usePageImage } from './usePageImage'
import { ChevronLeft, ChevronRight, Maximize2 } from '../../ui/icons'

// A PDF shown as the document itself: the server draws one page at a time as a PNG
// (backend/services/document_preview.py) — the same on desktop and a phone, where an embedded
// PDF viewer is unreliable, and for statements, whose file route only ever downloads.

export function DocumentPages({
  itemId,
  title,
  onExpand,
}: {
  itemId: string
  title: string
  /** Compact: the whole page fits a short box (a preview, not a reader); clicking it — or
   *  the expand button — calls this to open the full-size reader. */
  onExpand?: () => void
}) {
  const [page, setPage] = useState(1)
  const shown = usePageImage(itemId, page)
  // The last loaded page's count holds while the next page loads, so the pager stays put.
  const count = shown.count

  return (
    <div className={`vault-pages${onExpand ? ' vault-pages--compact' : ''}`}>
      {shown.loading ? (
        <div className="vault-page-skeleton" aria-label="Loading page" />
      ) : shown.error || !shown.url ? (
        <p className="vault-pages-error">Can't show this document here: {shown.error}</p>
      ) : onExpand ? (
        <button type="button" className="vault-page-open" onClick={onExpand} title="Open full size">
          <img
            className="vault-page-img"
            src={shown.url}
            alt={count ? `${title}, page ${page} of ${count}` : title}
          />
          <span className="vault-page-expand" aria-hidden="true">
            <Maximize2 size={13} strokeWidth={1.8} />
            Open
          </span>
        </button>
      ) : (
        <img
          className="vault-page-img"
          src={shown.url}
          alt={count ? `${title}, page ${page} of ${count}` : title}
        />
      )}
      {count && count > 1 ? (
        <div className="vault-pager">
          <button
            type="button"
            className="vault-icon-btn"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            aria-label="Previous page"
          >
            <ChevronLeft size={16} strokeWidth={1.8} />
          </button>
          <span className="vault-pager-label">
            Page {page} of {count}
          </span>
          <button
            type="button"
            className="vault-icon-btn"
            onClick={() => setPage((p) => Math.min(count, p + 1))}
            disabled={page >= count}
            aria-label="Next page"
          >
            <ChevronRight size={16} strokeWidth={1.8} />
          </button>
        </div>
      ) : null}
    </div>
  )
}
