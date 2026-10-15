// Reads docs/product/ directly off disk (frontmatter only, via gray-matter) so
// astro.config.mjs can build a hand-ordered Starlight sidebar and the landing
// page can link to a real "getting started" doc once one exists.
//
// This is intentionally separate from src/content.config.ts's content-collection
// loader (which Astro/Starlight use to actually render pages): Starlight's sidebar
// config is evaluated once, synchronously, while astro.config.mjs itself runs —
// before the content layer exists — so there is no way to ask the collection for
// its own entries at that point. Both this scan and the collection loader walk the
// same directory and derive the same doc_id (`<category>/<file-stem>`), so they
// never disagree about what a page's slug is.
//
// The corpus (docs/product/**) is owned by other agents/lanes and may be empty,
// partial, or briefly inconsistent while it's being written. This scan is
// deliberately fault-tolerant: an unreadable or unparsable file is skipped with a
// warning rather than aborting `astro dev` / `astro build` for everyone.
import fs from 'node:fs';
import path from 'node:path';
import matter from 'gray-matter';

// Resolved against process.cwd(), not import.meta.url: this module is imported
// both unbundled (astro.config.mjs, loaded directly by Node) and bundled
// (src/pages/index.astro's frontmatter, compiled through Vite's SSR pipeline
// for the static build) — Vite rewrites import.meta.url for the latter to a
// build-time chunk location, which silently broke a `../../../docs/product`
// relative-to-this-file resolution (the sidebar built fine from
// astro.config.mjs; the landing page's category cards came up empty). Astro
// always runs with cwd == the project root (docs-site/), from `astro dev`,
// `astro build`, and config loading alike, so this is stable everywhere.
export const CORPUS_ROOT = path.resolve(process.cwd(), '../docs/product');

/** Directories are the category (frozen contract) — this is the spec's sidebar ordering. */
export const CATEGORIES = [
  { id: 'getting-started', label: 'Getting Started' },
  { id: 'concepts', label: 'Concepts' },
  { id: 'features', label: 'Features' },
  { id: 'lucius', label: 'Lucius' },
  { id: 'connectors', label: 'Connectors' },
  { id: 'operations', label: 'Operations' },
  { id: 'troubleshooting', label: 'Troubleshooting' },
  { id: 'reference', label: 'Reference' },
];

function statusBadge(status) {
  if (status === 'coming-soon') return { text: 'Coming soon', variant: 'caution' };
  if (status === 'beta') return { text: 'Experimental', variant: 'note' };
  return undefined;
}

/**
 * Scan every category directory under CORPUS_ROOT for `*.md` files and return a
 * Map<categoryId, Array<{ docId, title, order, status }>>, each array sorted by
 * frontmatter `order` (ties broken alphabetically by title). Categories with no
 * files (corpus not written yet, or not yet reached) are simply absent from the
 * Map — callers must treat "no entry" as "empty", not as an error.
 */
export function scanCorpus(root = CORPUS_ROOT) {
  const byCategory = new Map();
  for (const { id: categoryId } of CATEGORIES) {
    const dir = path.join(root, categoryId);
    if (!fs.existsSync(dir)) continue;
    const entries = [];
    for (const filename of fs.readdirSync(dir)) {
      if (!filename.endsWith('.md')) continue;
      const filePath = path.join(dir, filename);
      try {
        const raw = fs.readFileSync(filePath, 'utf-8');
        const { data } = matter(raw);
        if (!data.title || typeof data.title !== 'string') {
          throw new Error('missing required "title" frontmatter key');
        }
        entries.push({
          docId: `${categoryId}/${filename.slice(0, -3)}`,
          title: data.title,
          order: typeof data.order === 'number' ? data.order : Number.MAX_SAFE_INTEGER,
          status: typeof data.status === 'string' ? data.status : 'stable',
        });
      } catch (err) {
        console.warn(`[docs-site] Skipping unparsable corpus file ${filePath}: ${err.message}`);
      }
    }
    entries.sort((a, b) => a.order - b.order || a.title.localeCompare(b.title));
    if (entries.length > 0) byCategory.set(categoryId, entries);
  }
  return byCategory;
}

/** features/ is 14 pages — one flat list nobody scans, and NESTED groups would
 *  make Starlight render collapsibles that hide pages behind a click.
 *
 *  So it is split into TOP-LEVEL sidebar sections instead, sitting beside
 *  Getting Started and Concepts. Nothing collapses, nothing is hidden.
 *
 *  The split follows what a user is trying to DO, in the app's own words:
 *  what they hold (Accounts/Overview), how they plan it (Plan/Commitments),
 *  how they invest it (Investments/Intel), and how they configure the thing.
 *  doc_ids are untouched — a page's URL is its public contract.
 */
const FEATURE_SECTIONS = [
  { label: 'Your money', docIds: [
    'features/dashboard',
    'features/accounts',
    'features/assets',
    'features/transactions',
    'features/importing',
  ]},
  { label: 'Planning', docIds: [
    'features/budgeting',
    'features/categories-payees',
    'features/commitments',
    'features/retirement-planning',
  ]},
  { label: 'Investing', docIds: [
    'features/investments',
    'features/equity-compensation',
    'features/intel',
  ]},
  { label: 'Setup', docIds: [
    'features/settings',
  ]},
];

/** Build a Starlight `sidebar` config array, grouped and ordered per CATEGORIES. */
export function buildSidebar(root = CORPUS_ROOT) {
  const byCategory = scanCorpus(root);
  const sidebar = [];
  for (const { id: categoryId, label } of CATEGORIES) {
    const entries = byCategory.get(categoryId);
    if (!entries) continue;
    const toItem = (entry) => ({
      slug: entry.docId,
      label: entry.title,
      badge: statusBadge(entry.status),
    });

    if (categoryId !== 'features') {
      sidebar.push({ label, items: entries.map(toItem) });
      continue;
    }

    // Split into top-level sections rather than one "Features" group.
    const byId = new Map(entries.map((e) => [e.docId, e]));
    const claimed = new Set();
    for (const section of FEATURE_SECTIONS) {
      const items = section.docIds
        .map((id) => byId.get(id))
        .filter(Boolean)
        .map((e) => (claimed.add(e.docId), toItem(e)));
      if (items.length) sidebar.push({ label: section.label, items });
    }
    // A page no section claims still appears — adding a feature doc must never
    // make it silently vanish from the nav.
    const rest = entries.filter((e) => !claimed.has(e.docId)).map(toItem);
    if (rest.length) sidebar.push({ label: 'More features', items: rest });
  }
  return sidebar;
}

/** doc_id of the first (lowest-`order`) doc in a category, or undefined if it has none yet. */
export function firstDocInCategory(categoryId, root = CORPUS_ROOT) {
  return scanCorpus(root).get(categoryId)?.[0]?.docId;
}
