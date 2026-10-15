#!/usr/bin/env node
/**
 * Build the app-embedded docs WITHOUT orphaning the assets older HTML still points at.
 *
 * Why this exists (owner, 2026-08-25, hours of it): Astro wipes its output directory and
 * mints new content-hashed `_astro/<name>.<hash>.css` filenames on every build. Any HTML a
 * browser is still holding — a cached copy, an open iframe, a page mid-navigation — then asks
 * for a stylesheet that no longer exists, gets a 404, and renders the documentation as raw
 * unstyled HTML: giant logo, blue underlined links, no layout. It does not look like a build
 * problem or a cache problem, which is what makes it so expensive to diagnose.
 *
 * Cache headers alone cannot fix this. They govern what a browser does NEXT; they cannot
 * reach a document it already holds. The only property that makes the failure impossible is
 * for the old asset to still be there when the old HTML asks for it.
 *
 * So: build normally, then restore any previous `_astro/` file the new build does not itself
 * provide. Filenames are content-hashed, so a restored file can never conflict with a new one
 * and never serves wrong content — identical content means an identical name. Stale files are
 * pruned once they are older than RETAIN_DAYS, so this does not grow without bound.
 */
import { spawnSync } from 'node:child_process'
import { cpSync, existsSync, mkdtempSync, readdirSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(fileURLToPath(new URL('.', import.meta.url)), '..')
const OUT = join(ROOT, 'dist-embed')
const ASSETS = join(OUT, '_astro')
const RETAIN_DAYS = 30

// 1. Stash the assets the previous build produced, if any.
let stash = null
if (existsSync(ASSETS)) {
  stash = mkdtempSync(join(tmpdir(), 'onw-docs-assets-'))
  cpSync(ASSETS, stash, { recursive: true })
}

// 2. Build (Astro clears dist-embed itself).
const res = spawnSync('npx', ['astro', 'build'], {
  cwd: ROOT,
  stdio: 'inherit',
  env: { ...process.env, EMBED_IN_APP: '1' },
  shell: process.platform === 'win32', // npx is npx.cmd on Windows, which spawn refuses to run directly
})
if (res.status !== 0) {
  if (stash) rmSync(stash, { recursive: true, force: true })
  process.exit(res.status ?? 1)
}

// 3. Restore every previous asset the new build did not re-emit, so HTML from the build
//    before this one still resolves.
let restored = 0
if (stash && existsSync(ASSETS)) {
  const cutoff = Date.now() - RETAIN_DAYS * 24 * 60 * 60 * 1000
  for (const name of readdirSync(stash)) {
    const from = join(stash, name)
    const to = join(ASSETS, name)
    if (existsSync(to)) continue
    if (statSync(from).mtimeMs < cutoff) continue // long dead; let it go
    cpSync(from, to, { recursive: true })
    restored += 1
  }
}
if (stash) rmSync(stash, { recursive: true, force: true })

console.log(
  `[build-embed] kept ${restored} asset(s) from the previous build so already-loaded pages ` +
    `keep resolving (pruned after ${RETAIN_DAYS} days).`,
)

// 4. The same content checks the published build runs. EMBED_IN_APP must be forwarded again
//    here — step 2's env override was scoped to that one subprocess, and this script's own
//    process never had it set, so the check script would otherwise default to checking dist/
//    (which this build never produces) instead of dist-embed/.
const post = spawnSync('node', ['scripts/check-internal-links.mjs'], {
  cwd: ROOT,
  stdio: 'inherit',
  env: { ...process.env, EMBED_IN_APP: '1' },
  shell: process.platform === 'win32', // npx is npx.cmd on Windows, which spawn refuses to run directly
})
process.exit(post.status ?? 0)
