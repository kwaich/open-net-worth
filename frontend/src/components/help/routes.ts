// TanStack route registration for the Help Center — a FACTORY, not a direct `createRoute`
// call, because the real `rootRoute` lives in router.tsx and importing it from here would
// create RouterShell.tsx <-> router.tsx <-> this file import cycle (router.tsx already
// imports RouterShell; RouterShell renders the Help Center by pathname, not by route match —
// see HelpDocsFrame's usage in RouterShell.tsx). router.tsx calls `createHelpRoutes(rootRoute)`
// and splices the two routes into its `routeTree.addChildren([...])` array, exactly like
// `assetRoute`/`quoteRoute` are wired in today. See
// docs/superpowers/notes/help-ui-requests.md for the exact router.tsx diff this lane could
// not apply itself (out of this lane's file scope).
//
// Neither route needs a `component` — mirrors every other route in router.tsx: RouterShell
// (the root route's component) renders everything itself by reading `location.pathname`
// rather than an <Outlet/>, so these two only need to exist so TanStack Router matches the
// path instead of falling through to its not-found handling.

import { createRoute, type AnyRoute } from '@tanstack/react-router'

export function createHelpRoutes(rootRoute: AnyRoute) {
  // Landing page — no doc selected, sidebar + a "choose a topic" welcome pane.
  const helpIndexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/help',
  })

  // One doc. doc_id is always exactly category/slug (docs/product/<category>/<file>.md is
  // one level deep) so two path params is correct here; docId.ts's own pathname parsing
  // (docId.ts) is deliberately more general in case that ever changes.
  const helpDocRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/help/$category/$slug',
  })

  return { helpIndexRoute, helpDocRoute }
}
