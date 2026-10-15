import { useCallback, useEffect, useRef, useState } from 'react'
import { docIdFromPathname, docIdToPath } from './docId'
import { resolveTheme, subscribeTheme } from '../../theme'
import { EmptyState } from '../ui/States'
import { TabSkeleton } from '../ui/TabSkeleton'

/** Where the app serves its own copy of the docs (backend/main.py mounts it here). */
const DOCS_BASE = '/docs'

/** The published site, used only when this instance has no local docs build. */
const DOCS_REMOTE = 'https://docs.opennetworth.ai'

/** Stamped once per frontend build (vite.config.ts `define`). Rides on the frame's URL so a
 *  new build can never reuse a cached copy of the docs HTML: the previous build's document is
 *  a different URL, so there is nothing to reuse. Needed because a docs rebuild renames every
 *  `_astro/<name>.<hash>.css` and deletes the old one — a stale HTML document therefore points
 *  at a stylesheet that 404s and the page renders completely unstyled. `Cache-Control:
 *  no-cache` on the server (backend/main.py) fixes that going forward, but it cannot reach a
 *  document a browser has ALREADY cached without one, and a top-level hard reload does not
 *  reliably re-fetch a nested frame's document. This does. */
declare const __BUILD_ID__: string
const BUILD = typeof __BUILD_ID__ === 'string' ? __BUILD_ID__ : 'dev'

/** Unique per app load, not per build. The build id alone repeats for as long as a dev server
 *  or a deployed build lives, so the frame keeps requesting a URL the browser may already hold
 *  a stale copy of — and a stale docs document names a stylesheet that a later docs rebuild
 *  deleted, which renders the whole site unstyled. A token that has never been seen before
 *  cannot resolve to a cached document, so the frame always gets today's HTML pointing at
 *  today's assets. Costs one small local HTML fetch; the hashed CSS/JS it names stay cached. */
const FRAME_TOKEN = `${BUILD}.${Date.now().toString(36)}`

/** Proof that /docs really is the docs — Starlight stamps this into every page it emits.
 *  A bare `res.ok` is NOT enough: with no docs build mounted, /docs/... used to fall through
 *  to the SPA catch-all, which answers 200 with the app's own index.html. The iframe then
 *  rendered a second, complete copy of the application inside the Help modal. The backend
 *  now 404s that path, but an older backend serving a freshly built frontend would still hit
 *  it, so the check stays here too: two independent guards against framing ourselves. */
const DOCS_MARKER = 'content="Starlight'

/** in-app /help path -> the docs URL to load. Starlight emits directory-style routes
 *  (`/docs/features/budgeting/`), so the trailing slash is load-bearing: without it the
 *  static mount answers with a redirect on every navigation. */
export function docsUrlForPathname(pathname: string, hash: string, origin = ''): string {
  const docId = docIdFromPathname(pathname)
  const base = origin ? `${origin}/` : `${DOCS_BASE}/`
  const path = docId ? `${base}${docId}/` : base
  // Local only — the published site is not ours to cache-bust, and its assets are stable.
  const versioned = origin ? path : `${path}?v=${encodeURIComponent(FRAME_TOKEN)}`
  return hash ? `${versioned}#${hash}` : versioned
}

/** The docs URL the frame is showing -> the in-app path that addresses it. Inverse of the
 *  above; returns null for anything outside the docs mount (a link that leaves the corpus). */
export function pathnameForDocsUrl(docsPathname: string): string | null {
  if (docsPathname !== DOCS_BASE && !docsPathname.startsWith(`${DOCS_BASE}/`)) return null
  const rest = docsPathname.slice(DOCS_BASE.length).replace(/^\/+/, '').replace(/\/+$/, '')
  return rest ? docIdToPath(rest) : '/help'
}

/** The app has three palettes; Starlight has two. `warm` IS a light palette (see
 *  tokens.source.mjs), so it maps to light rather than leaving the docs on dark. */
function starlightTheme(): 'dark' | 'light' {
  return resolveTheme() === 'dark' ? 'dark' : 'light'
}

export interface HelpDocsFrameProps {
  /** Full app pathname, e.g. "/help" or "/help/features/budgeting". */
  pathname: string
  /** Decoded URL hash, no leading '#'. */
  hash: string
  /** In-app navigation, so a click INSIDE the docs updates the address bar and back button. */
  onNavigate: (path: string) => void
  /** A docs link to an APP screen (/connectors?connector=email, /accounts…): open it in the app
   *  itself. Followed inside the frame, it loaded a second copy of the whole app inside Help. */
  onOpenApp?: (url: string) => void
}

/**
 * The in-app docs reader — an iframe over the app's own copy of the published documentation
 * site (`docs-site`, built with EMBED_IN_APP=1 and served at /docs).
 *
 * Why an iframe and not a React reader: there used to be one, ~700 lines of components plus
 * its own ~380-line block in index.css, rendering the same markdown corpus with a
 * hand-maintained imitation of the site's design. Every change to the published site's
 * look had to be ported across by hand, measured value by value, and the two drifted anyway.
 * This is the site itself — same build, same stylesheet, same search index — so there is
 * nothing left to keep in sync.
 *
 * Same-origin by construction (the app serves it), which is what lets this reach into the
 * frame to sync the theme and read back its location. Nothing is postMessage'd.
 */
export function HelpDocsFrame({ pathname, hash, onNavigate, onOpenApp }: HelpDocsFrameProps) {
  const frameRef = useRef<HTMLIFrameElement>(null)
  // Never point the frame at /docs on faith. If the embedded docs build is absent the mount
  // does not exist, and an iframe renders whatever the server DOES return — which, before
  // the SPA catch-all learned to refuse /docs, was the application itself, drawn a second
  // time inside its own Help modal. Probe first, and say so plainly when it is missing.
  const [state, setState] = useState<'checking' | 'local' | 'remote' | 'missing'>('checking')
  // What WE last told the frame to show. Guards the feedback loop: the frame navigating
  // pushes a route change, which re-renders this, which would otherwise reload the frame
  // at the URL it just arrived at — losing scroll position and the entry in its history.
  const shownRef = useRef<string | null>(null)

  const applyTheme = useCallback(() => {
    const frame = frameRef.current
    if (!frame) return
    try {
      const theme = starlightTheme()
      frame.contentDocument?.documentElement.setAttribute('data-theme', theme)
      // Also written to the frame's OWN storage, which is what Starlight's pre-paint script
      // reads: set only the attribute and every full document load paints the docs' default
      // palette for a frame before we can correct it. Same origin, so this is just storage.
      frame.contentWindow?.localStorage.setItem('starlight-theme', theme)
    } catch {
      // Frame torn down mid-navigation, or storage unavailable; the next load applies it.
    }
  }, [])

  // Follow the app's palette, both on load and whenever the user changes it in Settings.
  useEffect(() => subscribeTheme(applyTheme), [applyTheme])

  // LOCAL BUILD FIRST — this instance's own copy, served at /docs. It is offline, it matches
  // the version actually running here, and answering "how does this work" should not require
  // the network on a self-hosted app. The published site is the fallback, for an instance
  // that has no docs build.
  //
  // This was briefly reversed (owner, 2026-08-25) while the local copy was rendering
  // unstyled. That had nothing to do with which source was chosen: the mounts sent no
  // `Cache-Control` at all, so browsers replayed HTML that named hashed assets a later build
  // had deleted. Fixed at the root — `no-store` on HTML, immutable hashed assets
  // (backend/main.py), and docs-site/scripts/build-embed.mjs keeps the PREVIOUS build's
  // assets so an already-loaded page still resolves. Local can be trusted again.
  useEffect(() => {
    let cancelled = false
    const decide = async () => {
      try {
        const r = await fetch(`${DOCS_BASE}/?v=${encodeURIComponent(FRAME_TOKEN)}`, {
          headers: { Accept: 'text/html' },
          cache: 'no-store',
        })
        // A 200 is NOT enough: with no docs build mounted, /docs falls through to the SPA
        // catch-all and returns the app's own index.html — which the frame would render as a
        // second copy of the application inside the Help modal.
        if (r.ok && (await r.text()).includes(DOCS_MARKER)) return 'local' as const
      } catch {
        // Fall through to the published site.
      }
      try {
        // no-cors: the response is unreadable, but a rejected promise still separates
        // "reachable" from "no network", which is the only question being asked.
        await fetch(`${DOCS_REMOTE}/`, { mode: 'no-cors' })
        return 'remote' as const
      } catch {
        return 'missing' as const
      }
    }
    void decide().then((next) => {
      if (!cancelled) setState(next)
    })
    return () => {
      cancelled = true
    }
  }, [])

  // Drive the frame FROM the route. Only when the target differs from what we last set,
  // so a navigation that originated inside the frame doesn't bounce back as a reload.
  useEffect(() => {
    if (state !== 'local' && state !== 'remote') return
    const frame = frameRef.current
    if (!frame) return
    const target = docsUrlForPathname(pathname, hash, state === 'remote' ? DOCS_REMOTE : '')
    if (shownRef.current === target) return
    shownRef.current = target
    frame.src = target
  }, [pathname, hash, state])

  // Drive the route FROM the frame — a click on the docs' own sidebar, a cross-link, or its
  // search results. Without this the app's URL would stay on whatever page opened the modal,
  // so a refresh or a shared link would not come back to the page being read.
  const syncRouteFromFrame = useCallback(() => {
    applyTheme()
    const win = frameRef.current?.contentWindow
    if (!win) return
    let inAppPath: string | null
    try {
      inAppPath = pathnameForDocsUrl(win.location.pathname)
      if (inAppPath && win.location.hash) inAppPath += win.location.hash
    } catch {
      // A cross-origin document (an external link opened in-frame): nothing to mirror.
      return
    }
    if (!inAppPath) {
      // Same origin but outside the docs: the frame followed a link to an app screen. Hand it
      // to the app and put the frame back on the page it was showing.
      const appUrl = win.location.pathname + win.location.search + win.location.hash
      if (onOpenApp && win.location.origin === window.location.origin) {
        onOpenApp(appUrl)
        if (shownRef.current && frameRef.current) frameRef.current.src = shownRef.current
      }
      return
    }
    const [docPath = '', docHash = ''] = inAppPath.split('#')
    const target = docsUrlForPathname(docPath, docHash)
    if (shownRef.current === target) return
    shownRef.current = target
    onNavigate(inAppPath)
  }, [applyTheme, onNavigate, onOpenApp])

  // A click on a link to an app screen never navigates the frame: it opens in the app.
  const openAppLinks = useCallback(
    (e: MouseEvent) => {
      if (!onOpenApp || e.defaultPrevented || e.button !== 0) return
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
      const a = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null
      if (!a || a.target === '_blank') return
      const url = new URL(a.href, window.location.href)
      if (url.origin !== window.location.origin || pathnameForDocsUrl(url.pathname) !== null) return
      e.preventDefault()
      onOpenApp(url.pathname + url.search + url.hash)
    },
    [onOpenApp],
  )

  // Two listeners, because the docs navigate two different ways:
  //
  //   `load`             — a real document load: the first render, and any time the effect
  //                        above assigns a new src.
  //   `astro:page-load`  — every navigation the site makes ITSELF. The published site runs
  //                        Astro's ClientRouter (view transitions), so a click on its sidebar,
  //                        a cross-link or a search result is a same-document History API
  //                        navigation: the frame's URL changes and NO `load` event fires
  //                        anywhere. Measured, not assumed — with `load` alone the app's
  //                        address bar simply stopped following after the first click.
  //
  // The Astro listener lives on the frame's window and has to be re-attached after every real
  // document load, since that replaces the window it was bound to.
  // Only for the LOCAL copy. The published site is a different origin, so its document and
  // location are both unreadable from here: no theme sync, no address-bar sync. That is the
  // cost of the fallback, and the reason it is only a fallback.
  useEffect(() => {
    if (state !== 'local') return
    const frame = frameRef.current
    if (!frame) return
    const attach = () => {
      syncRouteFromFrame()
      try {
        // On the DOCUMENT: that is where Astro dispatches it. The document object survives a
        // client-side navigation (ClientRouter swaps its contents, it does not replace it),
        // so one listener covers every subsequent in-frame navigation.
        const doc = frame.contentDocument
        if (!doc) return
        doc.removeEventListener('astro:page-load', syncRouteFromFrame)
        doc.addEventListener('astro:page-load', syncRouteFromFrame)
        doc.removeEventListener('click', openAppLinks, true)
        doc.addEventListener('click', openAppLinks, true)
      } catch {
        // Cross-origin document (an external link followed in-frame): nothing to bind.
      }
    }
    frame.addEventListener('load', attach)
    if (frame.contentWindow) attach()
    return () => {
      frame.removeEventListener('load', attach)
      try {
        frame.contentDocument?.removeEventListener('astro:page-load', syncRouteFromFrame)
        frame.contentDocument?.removeEventListener('click', openAppLinks, true)
      } catch {
        /* already gone */
      }
    }
  }, [syncRouteFromFrame, openAppLinks, state])

  if (state === 'checking') {
    return (
      <div className="help-docs-state">
        <TabSkeleton variant="help" label="Opening Help…" header={false} />
      </div>
    )
  }

  if (state === 'missing') {
    return (
      <div className="help-docs-state">
        <EmptyState
          title="Help can't be reached"
          body="This instance has no local copy of the documentation, and docs.opennetworth.ai isn't reachable either. Run `make docs-site-embed` (or `make build`) to bundle it, and it will work offline from then on."
        />
      </div>
    )
  }

  return (
    <iframe
      ref={frameRef}
      className="help-docs-frame"
      title="Open Net Worth documentation"
      // No `src` attribute and no `onLoad`: the effects above own both, so React never
      // re-renders a src that would reload the frame and discard its own history.
    />
  )
}
