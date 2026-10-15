#!/usr/bin/env node
// Guards the one invariant behind a whole class of silent deploy bugs: every
// absolute URL this site emits about ITSELF must use the origin it was actually
// built for.
//
// The scar: robots.txt was a static public/ file with the production domain
// typed into it, while astro.config.mjs took `site` from SITE_URL. Build for any
// other origin and robots.txt still advertised a sitemap on the old domain — a
// 404 that nothing failed on, because nothing compared the two.
//
// Two checks, both cheap, run on every build:
//   1. robots.txt's Sitemap: URL shares the sitemap's own origin, and the file
//      it names actually exists in dist.
//   2. The production domain appears in no SOURCE file except the two allowed to
//      name it (the config default and this checker). That is what stops the
//      next hardcoded copy from being written in the first place.
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname.replace(/\/$/, '');
const DIST = join(ROOT, 'dist');
const PROD_DOMAIN = 'docs.opennetworth.ai';
// Allowed to contain the literal domain: the config that DEFINES the default,
// and this file, which must name it to check for it.
const ALLOW = new Set(['astro.config.mjs', join('scripts', 'check-site-origin.mjs')]);

const fail = (msg) => {
  console.error(`[check-site-origin] ${msg}`);
  process.exitCode = 1;
};

// ── 1. robots.txt agrees with the sitemap it points at ──────────────────────
const robotsPath = join(DIST, 'robots.txt');
if (!existsSync(robotsPath)) {
  fail('dist/robots.txt is missing — crawlers get no sitemap hint.');
} else {
  const robots = readFileSync(robotsPath, 'utf8');
  const m = robots.match(/^Sitemap:\s*(\S+)\s*$/m);
  if (!m) {
    fail('dist/robots.txt has no "Sitemap:" line.');
  } else {
    let sitemapUrl;
    try {
      sitemapUrl = new URL(m[1]);
    } catch {
      fail(`robots.txt Sitemap: is not an absolute URL: ${m[1]}`);
    }
    if (sitemapUrl) {
      // The file it names must exist where it says it does.
      const localPath = join(DIST, sitemapUrl.pathname);
      if (!existsSync(localPath)) {
        fail(`robots.txt points at ${sitemapUrl.href}, but ${relative(ROOT, localPath)} was not built.`);
      } else {
        // ...and the sitemap's own <loc> entries must use the same origin.
        const xml = readFileSync(localPath, 'utf8');
        const origins = new Set([...xml.matchAll(/<loc>(https?:\/\/[^/<]+)/g)].map((x) => x[1]));
        const wrong = [...origins].filter((o) => o !== sitemapUrl.origin);
        if (wrong.length) {
          fail(
            `origin mismatch: robots.txt says ${sitemapUrl.origin}, sitemap uses ${wrong.join(', ')}. ` +
              'Both must come from astro.config.mjs\'s `site`.',
          );
        }
      }
    }
  }
}

// ── 2. no new hardcoded copy of the production domain in source ─────────────
// dist-embed is build OUTPUT too (the in-app docs, `npm run build:embed`), not source: scanning it
// failed every local `npm run build` once the embed had been built in the same checkout.
const SKIP_DIRS = new Set(['node_modules', 'dist', 'dist-embed', '.astro', '.git']);
const offenders = [];
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const abs = join(dir, name);
    const st = statSync(abs);
    if (st.isDirectory()) {
      walk(abs);
      continue;
    }
    const rel = relative(ROOT, abs);
    if (ALLOW.has(rel) || ALLOW.has(rel.split(sep).join(sep))) continue;
    if (!/\.(m?[jt]s|astro|json|txt|md|css|html)$/.test(name)) continue;
    if (readFileSync(abs, 'utf8').includes(PROD_DOMAIN)) offenders.push(rel);
  }
};
walk(ROOT);
if (offenders.length) {
  fail(
    `hardcoded "${PROD_DOMAIN}" in: ${offenders.join(', ')}. ` +
      'Derive the origin from Astro\'s `site` instead, so a SITE_URL build rewrites it.',
  );
}

if (!process.exitCode) console.log('[check-site-origin] OK — robots.txt, sitemap and source agree on one origin.');
