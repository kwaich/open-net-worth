# 0025 — Server-side favicon proxy: reliable icons without third-party beaconing

- Status: Accepted
- Date: 2026-06-22
- Deciders: user (chose the backend proxy over DuckDuckGo/Google/leave-as-is after weighing the trade-off) + implementing agent
- Related: `backend/services/favicons.py`, `backend/api/favicon.py`, `frontend/src/utils/format.ts` (`faviconUrl`); reuses `core.net_guard.assert_public_url` (SSRF guard) and the `ai.search.base.plain_get_fetch` redirect-revalidation pattern

## Context

Publisher favicons (next to news headlines in `LatestDevelopments` / `NewsCardGrid`, via the `Favicon` component) were fetched **client-side** by pointing an `<img>` at `https://{domain}/favicon.ico` (`faviconUrl` in `frontend/src/utils/format.ts`). Two problems:

1. **Unreliable.** Many sites don't serve an icon at the literal `/favicon.ico` root — they declare it via `<link rel="icon" href="…">` at a custom path, redirect, or 404. Those rendered the blue `.favicon-fallback` box instead of an icon (the user reported "some websites can't load").
2. **A would-be fix via a third-party resolver (Google s2 / DuckDuckGo) leaks browsing.** Those services reliably resolve icons, but the user's browser then beacons *every viewed publisher domain* to that third party — contradicting the codebase's explicit "no Google beaconing" stance (the very reason `faviconUrl` used the direct path). Note the app was already inconsistent: `CitationChip` uses Google s2 while `Favicon` used the direct fetch.

The naive "just proxy `/favicon.ico` through the server" does **not** fix reliability — the icon still isn't at that path. Fixing reliability requires resolving the *declared* icon (parse the page's `<link rel=icon>`), which only a smart resolver (third-party) or our own server can do.

## Decision

A **server-side favicon proxy**: `GET /api/favicon?domain=…` (`backend/api/favicon.py`) backed by `backend/services/favicons.py`, with `faviconUrl` rewritten to return `/api/favicon?domain={host}`. The server resolves a site's real icon (HTML `<link rel=icon>` → `/favicon.ico` fallback), validates and caches it, and streams the bytes back.

Chosen over the alternatives because it is the only option that fixes reliability **and** honors the privacy stance: the server already fetches these domains for RSS/briefs, so resolving their favicon server-side adds **no new privacy exposure**, while the browser talks only to this instance. It also ends the `CitationChip`-vs-`Favicon` inconsistency (a fast-follow can point `CitationChip` at the same proxy).

Load-bearing properties:

1. **Reuses the existing SSRF seam — no new fetch infrastructure.** The domain is UNTRUSTED (feed/LLM-derived), so every fetch is validated by `core.net_guard.assert_public_url` and follows redirects **manually** (`httpx`, `follow_redirects=False`), re-validating each hop's `Location` — the exact pattern already shipped and tested in `ai.search.base.plain_get_fetch`. A public host that 30x-redirects to `169.254.169.254` / localhost / a private-LAN host is refused on the hop. (A `services`-tier module can't import `ai/`, so the ~15-line redirect loop is mirrored, not imported.)

2. **Magic-byte validation, not declared content-type.** The body is identified by image magic bytes (PNG/ICO/GIF/JPEG/WEBP/BMP); anything else is rejected. This deliberately drops **SVG** (no raster magic; an `<svg>` is scriptable) and an **HTML 404 page served as 200** — so the cache only ever holds a real raster image, and a direct hit can't execute. Bodies are **read raw** (`httpx.iter_raw` — no content-decoding) and capped on the **wire** (256 KB icon / 512 KB homepage HTML) with `Accept-Encoding: identity` + a `Content-Length` pre-check, so a **gzip-decompression bomb cannot expand past the cap in memory** (an undecoded gzip body simply fails magic-byte validation). Fetches are `https`-only (no cleartext downgrade on a redirect or parsed link), time out at 6 s, and follow a bounded redirect chain (≤3) over a bounded candidate set (≤4 parsed icons + `/favicon.ico`).

3. **Auth-gated AND rate-limited, not an open proxy.** The endpoint sits behind the app-wide `require_auth` gate like every other `/api` route. A same-origin `<img src="/api/favicon?…">` automatically carries the `onw_session` cookie, so it works without an exemption — only the logged-in operator can reach it, and `net_guard` is then defense-in-depth rather than the sole control. A per-client `SlidingWindowLimiter` (240/60 s) bounds the outbound fan-out so the endpoint can't be turned into an amplification/scan engine. Served with `X-Content-Type-Options: nosniff` + `Content-Security-Policy: default-src 'none'; sandbox`.

4. **CACHE_DIR cache, checkpoint-excluded.** Icons live under `CACHE_DIR/favicons/` (`{domain}.{ext}` bytes + an `index.json` manifest) with a positive TTL (30d) and a **negative cache** (1d) so a site with no resolvable icon isn't refetched on every render. Domain keys are validated to a clean host label (`^[a-z0-9.-]{1,253}$`), so they're safe as filenames (no path traversal). Writes go through `core.io.atomic_write_bytes` / `atomic_write_text` (pure FS, no write-hook → no checkpoint churn).

5. **Server-side DuckDuckGo fallback for bot-blocked sites (browser stays private).** Discovered during build: many corporate sites sit behind a Cloudflare "managed challenge" that **403s every programmatic request — even with a browser User-Agent** (e.g. `kestreldash.com`). A pure direct proxy *cannot* resolve those (neither could the old `<img>` direct fetch — the user's original complaint). So `_resolve`'s **last resort**, only after the page-parse and `/favicon.ico` both fail, is `https://icons.duckduckgo.com/ip3/{host}.ico` (DDG is CDN-allowlisted and returns a PNG). Crucially this runs **server-side**: the user's *browser* still talks only to this instance — DDG sees the instance asking for a domain's icon, never which user, and only for the minority of sites that bot-block a direct fetch. A well-behaved site is resolved directly and DDG is never touched. This is a deliberate, narrow softening of the "zero third party" goal in exchange for the reliability the feature exists to deliver; `host` is regex-validated so it's safe to interpolate into the DDG path.

A `Favicon` `onError` still shows the blue fallback when the proxy 404s (a fully unresolvable site), so the graceful-degradation behavior is unchanged.

## Consequences

- **Positive:** icons resolve for sites that declare a non-root icon path; zero third-party beaconing; one private+reliable source (ready to also back `CitationChip`); the SSRF surface reuses tested infrastructure and is operator-only.
- **Negative / costs:** the server now makes outbound HTTP to publisher domains for icons (same domains it already hits for news, but a new code path); first load for an uncached domain pays a network round-trip (then cached); SVG-only sites get the fallback box (accepted, to remove the SVG-XSS vector). No GC in v1 — the favicon cache grows unbounded until manually cleared (bounded in practice by the small set of news publishers; a TTL-sweep is a possible fast-follow).
- **Security review:** this touches external I/O (server-side fetch of untrusted domains) — a dedicated `/security-review` pass was run. It confirmed the SSRF guard, magic-byte validation, path-traversal safety, and auth posture as sound, and surfaced (now fixed) a HIGH decompression-bomb bypass of the size cap (closed via raw-byte reads + `identity` encoding + `Content-Length` pre-check) and a MEDIUM missing rate limit (closed via the per-client limiter); plus the `https`-only downgrade hardening. One **known, accepted limitation** remains: **DNS-rebinding TOCTOU** — `assert_public_url` resolves at validation time while httpx resolves again at connect time, so a rebinding attacker could differ the two. Closing it needs IP-pinned connections (a custom transport); deferred for parity with `ai.search.base.plain_get_fetch` and mitigated in depth by the rate limit + magic-byte validation + auth gate.
- **Reversibility:** `faviconUrl` is the single switch point — reverting to the direct fetch or swapping to a third-party resolver is a one-function change; the endpoint/service can be removed if abandoned.
