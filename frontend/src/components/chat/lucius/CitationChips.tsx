// Citation chips rendered under an assistant answer — ports prototype app.jsx:537–549.
// CSS classes (.lx-cites, .lx-cite, .lx-cite-n, .lx-cite-src) are provided by lucius.css
// (W1-CSS lane). This component is purely presentational; no state.
import type { Citation } from '../../../types/chat'

interface Props {
  cites: Citation[]
}

/** Row of source citation chips. Renders nothing when the list is empty. */
export function CitationChips({ cites }: Props) {
  if (!cites || cites.length === 0) return null

  return (
    <div className="lx-cites">
      {cites.map((c, i) => (
        <a
          key={i}
          className="lx-cite"
          href={c.url ?? '#'}
          target="_blank"
          rel="noreferrer"
          onClick={(e) => {
            if (!c.url) e.preventDefault()
          }}
          title={c.title ?? ''}
        >
          <span className="lx-cite-n">{c.n ?? i + 1}</span>
          <span className="lx-cite-src">{c.source ?? c.title}</span>
        </a>
      ))}
    </div>
  )
}
