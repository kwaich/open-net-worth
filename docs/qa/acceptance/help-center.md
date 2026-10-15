# Acceptance criteria — Help (`/help`)

Interaction-level criteria for the in-app documentation surface
(`frontend/src/components/help/HelpDocsFrame.tsx`), rewritten 2026-08-25 when the app's own
React reader was deleted in favour of showing the published site itself — see ADR
[`2026-08-25-the-in-app-docs-reader-is-the-published-site`](../../adr/2026-08-25-the-in-app-docs-reader-is-the-published-site.md).

**Scope changed with that decision.** Everything about how a *page* reads — sidebar ordering
and filtering, status badges, the table of contents, cross-link resolution, GFM tables, "See
also" — is now `docs-site`'s behaviour, identical to what `docs.opennetworth.ai` serves, and is
covered by that project's own `postbuild` checks (`check-internal-links`, `check-site-origin`)
and by the corpus lint (`make docs-lint`). It is deliberately NOT re-asserted here: an
acceptance file that re-tests Starlight is the same duplication this change removed.

What remains below is what the APP owns: how the docs are reached, framed, addressed, themed,
and what happens when they are absent.

Verified 2026-08-25 in a real browser against a disposable instance (never `:8080`). Where a
criterion says *measured*, the number came from that run.

---

## Entry point

### HELP-OPEN-FROM-SETTINGS
Settings → Help → **Open** opens Help as a `size="workspace"` modal over the tab you were on.
It is a router navigation, not a page load: no full reload, no loss of app state.
Closing (×, Escape, backdrop) returns to Settings — not past it to whatever preceded Settings.

### HELP-DEEP-LINK
`/help` opens the docs landing; `/help/<category>/<slug>` opens that page directly. Both work
on a cold load and on refresh. A cold `/help` closed with × falls back to a real page
(`/dashboard`), never a dead end.

### HELP-UNDERNEATH
A real tab renders behind the modal — Help is an overlay, never an empty shell. The sidebar
highlights the tab underneath (Help has no nav row of its own), and `/help` is never persisted
as the tab to restore on next launch.

---

## The frame

### HELP-FRAME-IS-THE-DOCS
The frame renders the documentation — Starlight markup (`.sl-markdown-content`) present, and
**no app chrome inside it**: no ticker strip, no chat panel, no tab bar.

### HELP-NEVER-FRAMES-THE-APP
*Regression, owner-reported 2026-08-25.* The docs mount exists only when
`docs-site/dist-embed/` does. Without it, `/docs/...` reached the SPA catch-all, which answers
unmatched paths with the app's own `index.html`, and the frame rendered a second complete copy
of the application inside the modal.

Two independent guards, both required:
1. `/docs` 404s when the build is absent (`_SPAStaticFiles._NEVER_SHELL`) — body is
   `{"detail":"Not Found"}`, not app HTML.
2. The frame refuses any `/docs` response lacking Starlight's `generator` marker, **even a
   200**. Not redundant: an older backend serving a freshly built frontend returns 200 for
   `/docs/`, which a bare `res.ok` check accepts.

Test the second by stubbing `/docs/` as `200 text/html` containing `<div id="root">`: the
frame must not load it.

### HELP-EMBED-CHROME-TRIMMED
The embedded build hides the wordmark, the theme switch and social links — chrome the modal
already provides. Search survives the trim and takes the reclaimed width. *(Their presence is
the tell that the remote fallback is being shown instead.)*

---

## Addressing and theme

### HELP-URL-FOLLOWS-THE-FRAME
Clicking inside the docs — sidebar, cross-link, search result — updates the app's address bar
to the matching `/help/...` path. *Measured:* `/help/concepts/net-worth` →
`/help/getting-started/your-first-month`.

Load-bearing detail: the site runs Astro's ClientRouter, so an in-frame click is a
same-document History navigation and **no `load` event fires**. The sync listens for
`astro:page-load` on the frame's `document`. With `load` alone (or React's `onLoad` prop) the
address bar silently stops following after the first click — that is the failure this
criterion exists to catch.

Navigation uses `replace`, so reading through the docs does not stack one app history entry per
page; the frame keeps its own back/forward.

### HELP-THEME-FOLLOWS-THE-APP
The docs render in the app's palette (`warm` maps to Starlight's light). Applied to the frame's
`data-theme` **and** its `localStorage['starlight-theme']` — attribute only leaves a flash of
the wrong palette on each full load, since Starlight's pre-paint script reads storage.

Local copy only: the remote fallback is cross-origin and cannot be themed.

---

## Degraded states

### HELP-SOURCE-IS-THE-PUBLISHED-SITE
Help frames `https://docs.opennetworth.ai/<path>`. The locally-built copy is the OFFLINE
fallback only, used when the site is unreachable. *(Verified the host permits framing: no
`X-Frame-Options`, no `frame-ancestors`.)*

Because the site is a different origin, HELP-URL-FOLLOWS-THE-FRAME and
HELP-THEME-FOLLOWS-THE-APP apply only to the offline fallback — against the published site the
app's URL stays on `/help` and the docs use their own palette.

### HELP-OFFLINE-AND-UNBUILT
No local build **and** no network: an honest empty state naming the fix
(`make docs-site-embed`) — never a blank frame, never a browser error page.

### HELP-CHECKING
Between mount and probe resolution, a loading state — the frame is never pointed at `/docs` on
faith.

---

## Responsive

### HELP-MOBILE
On a phone the modal renders and the docs are readable and scrollable, with no horizontal page
overflow. The embedded site's own responsive behaviour (collapsed nav, its mobile table of
contents) is `docs-site`'s to own, not this file's.
