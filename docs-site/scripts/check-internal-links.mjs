#!/usr/bin/env node
// Post-build guard: fails the build if the rendered site contains a dead
// internal link. Runs automatically as `postbuild` (see package.json) so a
// broken link fails `npm run build` locally AND in CI — not just a silent
// 404 nobody notices until a reader clicks it.
//
// This exists because of a real bug found while building this site: Astro's
// markdown pipeline does not resolve the corpus's relative `.md` cross-links
// on its own (see src/lib/remark-resolve-corpus-links.mjs, which fixes the
// links it CAN resolve). This script is the safety net for the ones it
// can't — a `.md` link that somehow survives, or any other internal href
// that doesn't point at a page or asset that actually exists in `dist/`.
import fs from 'node:fs';
import path from 'node:path';

// Matches astro.config.mjs's own outDir choice — EMBED_IN_APP=1 (build:embed) builds into
// dist-embed/, never dist/. A hardcoded 'dist' here only "worked" when a stale dist/ from an
// earlier plain `npm run build` happened to still be sitting on disk; a genuinely fresh
// checkout has no dist/ at all in the embed-only path and this failed immediately.
const DIST = path.resolve(process.cwd(), process.env.EMBED_IN_APP === '1' ? 'dist-embed' : 'dist');
// Must mirror astro.config.mjs's own `base` resolution exactly, not just read SITE_BASE in
// isolation — that config defaults to '/docs/' under EMBED_IN_APP with no SITE_BASE set, and
// this script silently disagreeing with it is what turned every real, working link in an
// embed build into a false "dead link" (looking for dist-embed/docs/x instead of dist-embed/x).
const BASE = (
  process.env.SITE_BASE || (process.env.EMBED_IN_APP === '1' ? '/docs/' : '/')
).replace(/\/+$/, '') || '';

const HREF_RE = /<a\b[^>]*\shref="([^"]*)"/gi;
const EXTERNAL_RE = /^([a-z][a-z0-9+.-]*:|\/\/)/i;

function listHtmlFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      // Pagefind's own output is third-party generated tooling, not authored
      // content — not this guard's concern.
      if (entry.name === 'pagefind') continue;
      out.push(...listHtmlFiles(full));
    } else if (entry.name.endsWith('.html')) {
      out.push(full);
    }
  }
  return out;
}

/** True if `distRelativePath` resolves to a real file Astro actually emitted. */
function targetExists(distRelativePath) {
  const clean = distRelativePath.split('?')[0].split('#')[0];
  if (clean === '' || clean === '/') return fs.existsSync(path.join(DIST, 'index.html'));
  const direct = path.join(DIST, clean);
  if (fs.existsSync(direct) && fs.statSync(direct).isFile()) return true;
  const asIndex = path.join(DIST, clean, 'index.html');
  if (fs.existsSync(asIndex)) return true;
  if (!clean.endsWith('/')) {
    const withIndex = path.join(DIST, `${clean}.html`);
    if (fs.existsSync(withIndex)) return true;
  }
  return false;
}

function checkFile(file) {
  const html = fs.readFileSync(file, 'utf-8');
  const problems = [];
  for (const match of html.matchAll(HREF_RE)) {
    const href = match[1];
    if (!href || href.startsWith('#') || EXTERNAL_RE.test(href) || href.startsWith('mailto:')) {
      continue;
    }

    const pathOnly = href.split('?')[0].split('#')[0];
    if (/\.mdx?$/i.test(pathOnly)) {
      problems.push(`links to a raw markdown source (${href}) — should be a rendered site route`);
      continue;
    }

    if (href.startsWith('/')) {
      // Strip the configured base before checking against dist's own layout
      // (dist/ is never itself nested under a base-named directory — base
      // only changes how Astro *prefixes* the links it prints).
      const withoutBase =
        BASE && href.startsWith(BASE) ? href.slice(BASE.length) || '/' : href;
      if (!targetExists(withoutBase)) {
        problems.push(`points at "${href}", which is not a page or asset in dist/`);
      }
    } else {
      // A relative link (not `.md`, already handled above): resolve against
      // this page's own directory.
      const pageDir = path.dirname(path.relative(DIST, file));
      const resolved = path.posix.normalize(`/${path.join(pageDir, href)}`);
      if (!targetExists(resolved)) {
        problems.push(`relative link "${href}" does not resolve to a real page (tried ${resolved})`);
      }
    }
  }
  return problems;
}

function main() {
  if (!fs.existsSync(DIST)) {
    console.error(`[check-internal-links] dist/ not found at ${DIST} — run \`astro build\` first.`);
    process.exit(1);
  }

  const files = listHtmlFiles(DIST);
  let totalProblems = 0;
  for (const file of files) {
    const problems = checkFile(file);
    if (problems.length === 0) continue;
    totalProblems += problems.length;
    const relFile = path.relative(DIST, file);
    for (const problem of problems) {
      console.error(`[check-internal-links] dist/${relFile}: ${problem}`);
    }
  }

  if (totalProblems > 0) {
    console.error(
      `\n[check-internal-links] FAILED — ${totalProblems} dead internal link(s) across ${files.length} page(s).`
    );
    process.exit(1);
  }

  console.log(`[check-internal-links] OK — checked ${files.length} page(s), no dead internal links.`);
}

main();
