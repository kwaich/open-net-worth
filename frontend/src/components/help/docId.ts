// doc_id <-> in-app pathname, in ONE place — HelpDocsFrame (pathname parsing, no typed router
// params so it works whether or not the routes are wired into router.tsx's tree yet) and
// routes.ts (the TanStack route registration itself) both read this so they can never drift.
//
// doc_id = the path under docs/product/ with .md stripped, e.g. "features/budgeting" — always
// category/slug per the corpus layout (docs/product/<category>/<file>.md, one level deep), but
// parsing here stays generic (any number of segments) rather than assuming exactly two.

export const HELP_BASE = '/help'

/** doc_id -> the deep-linkable in-app path, e.g. "features/budgeting" -> "/help/features/budgeting". */
export function docIdToPath(docId: string): string {
  return `${HELP_BASE}/${docId}`
}

/** The current pathname -> doc_id, or null on the bare /help landing page (no doc selected). */
export function docIdFromPathname(pathname: string): string | null {
  if (!isHelpPathname(pathname)) return null
  const rest = pathname.slice(HELP_BASE.length).replace(/^\/+/, '').replace(/\/+$/, '')
  return rest || null
}

/** True for /help and every /help/* sub-path — what RouterShell uses to decide whether to
 *  render the Help Center in place of the tab tree. */
export function isHelpPathname(pathname: string): boolean {
  return pathname === HELP_BASE || pathname.startsWith(`${HELP_BASE}/`)
}
