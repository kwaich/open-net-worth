import type { APIRoute } from 'astro';

// robots.txt is GENERATED, not a static public/ file, so its Sitemap: line can
// never disagree with the origin the sitemap is actually built at. Both derive
// from astro.config.mjs's `site` (SITE_URL, defaulting to the production
// domain), so a deploy to another origin rewrites the two together.
//
// The scar: robots.txt used to be public/robots.txt with the production domain
// typed into it, while `site` was already env-driven. Any build for a different
// origin advertised a sitemap that 404s — and nothing failed, because nothing
// compared them. scripts/check-site-origin.mjs now does, on every build.
//
// No fallback origin on purpose. `site` is unset only if someone removes it
// from astro.config.mjs, and guessing a domain there would reintroduce exactly
// the silently-wrong value this file exists to prevent — so fail the build.
export const GET: APIRoute = ({ site }) => {
  if (!site) {
    throw new Error(
      "robots.txt needs `site` in astro.config.mjs to name the sitemap's origin.",
    );
  }

  // Everything published here is public product documentation — nothing to
  // disallow. Keep the trailing newline: some crawlers are fussy about it.
  const body = `User-agent: *\nAllow: /\n\nSitemap: ${new URL('sitemap-index.xml', site).href}\n`;

  return new Response(body, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
};
