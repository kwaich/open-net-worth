// Prepares AI-authored artifact documents for the chat's sandboxed iframe.
//
// The document executes ONLY inside <iframe sandbox="allow-scripts"> (never
// allow-same-origin): a null origin, so no cookies/localStorage/parent DOM and no
// credentialed API calls. This module adds the second layer: an egress-blocking
// CSP <meta> injected as the FIRST head element, so scripts inside the frame
// cannot fetch/beacon any financial data out (default-src 'none').
//
// Any model-emitted CSP meta is stripped first. (Multiple CSP metas intersect, so
// a permissive one could never RELAX ours — stripping just keeps the document's
// effective policy legible and ours authoritative.)

const CSP_HTML =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
  'img-src data: blob:; font-src data:'
const CSP_SVG = "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:"

const CSP_META_RE = /<meta[^>]*http-equiv\s*=\s*["']?content-security-policy["']?[^>]*>/gi

// The frame is auto-sized to the document's content height, but scrollHeight is
// integer-floored — a sub-pixel overflow renders a classic (white) scrollbar
// sliver. Hide scrollbars inside the document entirely: the common case fits
// exactly, and clamped-at-max documents stay wheel/touch-scrollable.
const FRAME_STYLE =
  '<style>*{scrollbar-width:none;-ms-overflow-style:none}*::-webkit-scrollbar{display:none}</style>'

function cspMeta(policy: string): string {
  return `<meta http-equiv="Content-Security-Policy" content="${policy}">` + FRAME_STYLE
}

// ── Content-height probe ──────────────────────────────────────────────────────
// The parent CANNOT measure a null-origin frame (that's the sandbox working), so
// a tiny script inside the document posts its scroll height up via postMessage.
// It sends only a number; the parent (ArtifactCard) verifies event.source is its
// own frame and clamps the value, so a hostile document can at worst pick its own
// rendered height — which it already controls via its content.
const HEIGHT_PROBE_MARK = '__onwArtifact'
// Measure the BODY's content height, never documentElement.scrollHeight: the
// latter is floored at the viewport height, which ratchets — one transiently
// tall layout would grow the frame and the report could then never shrink back
// (the big-empty-space bug). body.scrollHeight + its viewport offset tracks the
// real content in both directions.
const HEIGHT_PROBE =
  '<script>(function(){var last=0;' +
  'function h(){var b=document.body;if(!b)return 0;' +
  'var r=b.getBoundingClientRect();var cs=getComputedStyle(b);' +
  'var mb=parseFloat(cs.marginBottom)||0;var top=Math.max(0,Math.ceil(r.top));' +
  'return Math.max(b.scrollHeight+top,Math.ceil(r.bottom+mb))}' +
  'function post(){var v=h();if(v>0&&Math.abs(v-last)>2){last=v;' +
  'parent.postMessage({' +
  HEIGHT_PROBE_MARK +
  ":'height',height:v},'*')}}" +
  "window.addEventListener('load',post);" +
  'if(typeof ResizeObserver!=="undefined"){try{new ResizeObserver(post).observe(document.body)}catch(e){}}' +
  'setTimeout(post,0);setTimeout(post,250);setTimeout(post,1000)})()</scr' +
  'ipt>'

/** Parse a height report posted by the in-frame probe. Returns null for anything
 * that isn't a well-formed {__onwArtifact:'height', height:n>0} payload. */
export function artifactHeightFromMessage(data: unknown): number | null {
  if (typeof data !== 'object' || data === null) return null
  const d = data as Record<string, unknown>
  if (d[HEIGHT_PROBE_MARK] !== 'height') return null
  const h = d.height
  return typeof h === 'number' && Number.isFinite(h) && h > 0 ? h : null
}

/** The iframe `sandbox` attribute for an artifact — `allow-scripts` only, NEVER
 * `allow-same-origin` (a null origin: no cookies, no parent DOM, no credentialed API calls).
 * SVG artifacts run with scripts fully disabled (no probe; sized from svgAspectRatio instead). */
export function sandboxFor(type: 'html' | 'svg'): string {
  return type === 'svg' ? '' : 'allow-scripts'
}

/** height/width ratio of an SVG document (viewBox preferred, then width/height
 * attributes) — SVG artifacts run with scripts disabled, so the probe can't run
 * and the card sizes the frame from the intrinsic aspect ratio instead. */
export function svgAspectRatio(svg: string): number | null {
  const tag = svg.match(/<svg\b[^>]*>/i)?.[0]
  if (!tag) return null
  const vb = tag.match(
    /viewBox\s*=\s*["']\s*[-\d.eE+]+[\s,]+[-\d.eE+]+[\s,]+([\d.eE+]+)[\s,]+([\d.eE+]+)/i,
  )
  if (vb) {
    const w = parseFloat(vb[1] ?? '')
    const h = parseFloat(vb[2] ?? '')
    if (w > 0 && h > 0) return h / w
  }
  const wAttr = tag.match(/\bwidth\s*=\s*["']?([\d.]+)/i)
  const hAttr = tag.match(/\bheight\s*=\s*["']?([\d.]+)/i)
  if (wAttr && hAttr) {
    const w = parseFloat(wAttr[1] ?? '')
    const h = parseFloat(hAttr[1] ?? '')
    if (w > 0 && h > 0) return h / w
  }
  return null
}

/** Append the height probe inside <body> (before its close tag when present). */
function injectProbe(html: string): string {
  const bodyClose = html.match(/<\/body\s*>/i)
  if (bodyClose && bodyClose.index !== undefined) {
    return html.slice(0, bodyClose.index) + HEIGHT_PROBE + html.slice(bodyClose.index)
  }
  return html + HEIGHT_PROBE
}

/** Inject the egress-blocking CSP + the height probe into a full/partial HTML document. */
function prepareHtml(html: string): string {
  const meta = cspMeta(CSP_HTML)
  const stripped = html.replace(CSP_META_RE, '')

  const headMatch = stripped.match(/<head[^>]*>/i)
  if (headMatch && headMatch.index !== undefined) {
    const at = headMatch.index + headMatch[0].length
    return injectProbe(stripped.slice(0, at) + meta + stripped.slice(at))
  }
  const htmlMatch = stripped.match(/<html[^>]*>/i)
  if (htmlMatch && htmlMatch.index !== undefined) {
    const at = htmlMatch.index + htmlMatch[0].length
    return injectProbe(stripped.slice(0, at) + `<head>${meta}</head>` + stripped.slice(at))
  }
  // Fragment (no <html>/<head>): a leading <meta> lands in the parser's implicit
  // head. Keep a doctype (if any) first so the frame doesn't fall into quirks mode.
  const doctypeMatch = stripped.match(/^\s*<!doctype[^>]*>/i)
  if (doctypeMatch) {
    const at = doctypeMatch[0].length
    return injectProbe(stripped.slice(0, at) + meta + stripped.slice(at))
  }
  return injectProbe(meta + stripped)
}

/** Wrap an SVG document in a minimal page that lets the svg fill the card width
 * (scripts are additionally disabled by the card's sandbox="" for svg artifacts —
 * no probe; the card sizes the frame from svgAspectRatio instead). */
function prepareSvg(svg: string): string {
  const body = svg.replace(CSP_META_RE, '')
  return (
    '<!doctype html><html><head>' +
    cspMeta(CSP_SVG) +
    '<style>html,body{margin:0}svg{display:block;width:100%;height:auto}</style>' +
    `</head><body>${body}</body></html>`
  )
}

/** The srcDoc string for an artifact document. Never render artifact bytes any
 * other way (no dangerouslySetInnerHTML, no same-origin URL). */
export function prepareArtifactSrcDoc(content: string, artifactType: 'html' | 'svg'): string {
  return artifactType === 'svg' ? prepareSvg(content) : prepareHtml(content)
}
