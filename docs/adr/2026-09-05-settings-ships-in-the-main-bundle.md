---
title: Settings ships in the main bundle; only its sections are lazy chunks
status: Accepted
date: 2026-09-05
deciders: repo owner + Claude (hardening-loop turn)
---

# 2026-09-05. Settings ships in the main bundle; only its sections are lazy chunks

## Context

The Settings overlay was a lazy chunk (`Settings-*.js` ≈ 30 KB raw / 10 KB gzipped, plus
`Settings-*.css` ≈ 39 KB raw / 6 KB gzipped), mounted under a `Suspense` boundary whose
fallback was `null`. On the first open of a session nothing rendered — no sheet, no scrim, no
skeleton — until that chunk had downloaded and run. On a phone: tap the Settings row, the More
sheet closes, a blank beat, then the sheet slides up. Nothing prefetched the chunk. The owner
asked whether Settings "waits for loading all information before showing"; it waited for code,
not data.

Claude first proposed prefetch + an instant skeleton fallback and argued against folding
Settings into the main bundle over its 38 KB of CSS. The owner asked why 38 KB was a concern.
Measured against the bundle every boot already loads (`index-*.js` 473 KB gzipped, `index-*.css`
122 KB gzipped), Settings is ~17 KB gzipped — about 3% — served from the owner's own machine
and cached for the session. The split saved nothing measurable on any device.

## Decision

1. **`Settings` is a static import in `RouterShell`.** The `Suspense fallback={null}` around it
   is removed (the `ErrorBoundary` stays). The first open renders the sheet immediately, like
   every later one.
2. **The sections inside stay lazy** (`lazyChunk` per section, hover/press prefetch on their
   rows, `SectionFallback` skeleton in the pane). The largest is ~6 KB gzipped and none is
   opened by most sessions; their prefetch already hides the load, so there is no felt cost to
   keep them out of the boot path — but the same reasoning would justify making them static too
   if that ever changes.
3. **The bar for lazy-loading a screen in this app is "measurable at boot on the owner's own
   network", not "web-perf hygiene".** A route-level chunk also adds a failure mode: a tab that
   predates a rebuild can 404 on it (the service-worker removal note records that scar), and
   Settings is the screen most likely to be opened right after a deploy.

## Consequences

- Main bundle: +~10 KB JS, +~5 KB CSS gzipped. `settings.css` is scoped to `.onw-settings` /
  `.rd-set-*`, so joining the global stylesheet changes no cascade outside the overlay.
- One fewer chunk that can go stale across a rebuild.
- `docs/qa/acceptance/settings-redesign.md` A11 pins the first-open behaviour.

## Alternatives considered

- **Prefetch the chunk on More-sheet open / sidebar hover** — would hide the wait on most taps
  but keep the chunk and its post-rebuild 404 mode, plus a hook to maintain. Not worth it for
  17 KB.
- **Render the bare sheet + skeleton as the Suspense fallback** — an instant shell, but a second
  code path for a state that only exists because of the split; removing the split removes the
  state.
- **Make the sections static too** — deferred, not rejected; see Decision (2).

## Agent provenance

- **Decided by:** repo owner ("Do it"), after challenging Claude's 38 KB objection.
- **Lane / branch:** `main` (direct, per the owner's standing workflow).
- **Driving artifacts:** `frontend/src/RouterShell.tsx` (static import, Suspense removed),
  `docs/qa/acceptance/settings-redesign.md` A11.
- **Why this way (one line):** the only wait the owner could feel was a download that saved
  nothing on their own network, so the honest fix was to stop deferring it.
