# The in-app docs reader IS the published site

- **Date:** 2026-08-25
- **Status:** Accepted
- **Supersedes in part:** `docs/superpowers/specs/2026-08-23-product-docs-and-launch-design.md`
  Component 3 (the React Help Center), and the CLAUDE.md Docs-map line describing
  `docs/product/` as powering "the in-app `/help` tab **and** the public `docs-site/`" as two
  renderers of one corpus.

## Context

`docs/product/` was rendered twice, by two independent readers:

- **`docs-site/`** — Astro + Starlight, deployed to `docs.opennetworth.ai`, with a
  hand-tuned theme in `src/styles/custom.css` (~1,500 lines).
- **`frontend/src/components/help/`** — a React reader (`HelpCenter`, `HelpDocPage`,
  `HelpSidebar`, `HelpStatusBadge`, `markdownLink`, `useHelpDocs`) plus a ~380-line `.help-*`
  block in `index.css`, reading the same markdown over `GET /api/help/docs`.

Only the *content* was shared. Every question of presentation — type scale, heading rhythm,
link treatment, code chrome, sidebar density, where the table of contents lives — existed
twice and had to be kept in agreement by hand.

On 2026-08-24 the owner reported that in-app Help "doesn't show same style as the standalone
docs". It didn't, and measuring the two showed why: same font (Plus Jakarta Sans), different
scales — 13px body vs 14px, **20px `h2` vs 17px**, a boxed inline table of contents where the
site has a sticky right rail. Closing the gap took a computed-style diff, element by element,
and produced a stylesheet that matched *that afternoon's* build of the site and nothing after
it. The owner's conclusion — "I don't want to have to update or maintain two versions" — is
the correct one: the port was not a fix, it was the defect restated in more code.

## Decision

**There is one reader.** `docs-site` is built a second time (`npm run build:embed`,
`EMBED_IN_APP=1`) with a `/docs/` base into `docs-site/dist-embed/`; the backend serves that
directory at `/docs`; the Help modal shows it in an iframe
(`frontend/src/components/help/HelpDocsFrame.tsx`).

Same build, same `custom.css`, same components, same Pagefind index. The app has no
documentation stylesheet of its own and cannot drift from the published site, because there is
nothing left to drift from.

Deleted: the six reader components and their tests, `useHelpDocs`, and the `.help-*` CSS block
— 1,985 lines, replaced by ~150 lines of frame plumbing and ~50 of `embed.css`.

`embed.css` is the ONLY difference between the two builds, and only removes chrome the modal
already provides: the wordmark (the modal's head names the surface), the theme switch (the app
owns the palette), and outbound social links. Type, colour and layout are untouched — that is
the entire point.

## Consequences

**What the iframe buys, and why it must be same-origin.** The app writes `data-theme` and
`starlight-theme` into the frame so the docs follow the app's palette, and reads the frame's
location so the address bar follows the page being read. Both require same-origin access.

**Navigation needs `astro:page-load`, not `load`.** The site runs Astro's ClientRouter, so an
in-frame click is a same-document History API navigation: the URL changes and no `load` event
fires anywhere. React's `onLoad` prop is doubly wrong here — it also only fires once. The
listener is attached natively to the frame's `document` (not its window, where the event does
not arrive) and re-attached after each real document load.

**A missing build must look missing.** The docs mount is registered only when `dist-embed`
exists. Without it, `/docs/...` reached the SPA catch-all, which answers unmatched paths with
the app's own `index.html` — and the iframe rendered a complete second copy of the application
inside the Help modal, ticker strip and chat panel included (owner, 2026-08-25). Two
independent guards now: `docs/` joins `api/` on `_SPAStaticFiles._NEVER_SHELL`, and the frame
refuses any `/docs` response that does not carry Starlight's `generator` marker. The second
guard is not redundant — an older backend serving a freshly built frontend returns **200** for
`/docs/`, which a bare `res.ok` check would have accepted.

**`docs.opennetworth.ai` IS what Help shows** (owner, 2026-08-25, after the local copy caused
a long unstyled-page incident: "just show the remote one"). The locally-built copy is now only
the offline fallback, reached when the site is unreachable. Verified framable: the host sends
no `X-Frame-Options` and no `frame-ancestors`.

Accepted costs, since they are real: the site describes `main`, not necessarily this
instance's version; and being a different ORIGIN, neither the theme sync nor the address-bar
sync above runs against it — while browsing the docs the app's URL stays on `/help`.

**Build cost.** `make build` now depends on `docs-site-embed`; `scripts/setup.sh` and the
Dockerfile build it too. A fresh clone therefore needs `docs-site`'s npm deps. CI does not run
`make build` (it mirrors the steps directly) and so does not build the embedded docs — nothing
in CI serves the app, but it does mean CI never exercises this path.

**`GET /api/help/docs` now has no consumer.** Kept deliberately:
`tests/test_product_docs_security.py` uses that router to prove a `doc_id` arriving in a URL
cannot traverse the filesystem, and the service behind it still backs the `search_docs` /
`read_doc` tools. Removing the route means finding that proof another home.

**Still duplicated:** the category list and the landing blurbs
(`services/product_docs.py::CATEGORY_TITLES` / `CATEGORY_BLURBS` vs
`docs-site/src/lib/corpus.mjs::CATEGORIES` / `index.astro::categoryBlurbs`). Those feed the API
and the site's own landing page, not the reader, so this change does not remove them.
`tests/test_product_docs_service.py` pins that every category has a blurb, but not that the two
tables agree on wording.

## Agent provenance

Built by Claude (Opus 5) on 2026-08-25, at the owner's explicit direction after an afternoon
spent hand-porting the site's stylesheet into the app. The owner made the call to collapse to
one reader; the iframe/same-origin design, the two anti-self-framing guards, and the remote
fallback are the assistant's. Every claim above about event behaviour, framing and fallback was
verified in a real browser against a disposable instance, not reasoned about — the
`astro:page-load` and 200-but-not-docs findings in particular were both discovered by
measurement after the "obvious" implementation silently failed.
