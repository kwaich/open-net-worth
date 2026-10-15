import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';
import { docsSchema } from '@astrojs/starlight/schema';

// THE ONE-CORPUS RULE: this reads docs/product/**/*.md directly — the single
// source of truth also read by backend/services/product_docs.py and the in-app
// /help route. This site never holds its own copy of a corpus file. See
// docs/superpowers/specs/2026-08-23-product-docs-and-launch-design.md.
//
// Starlight's own `docsLoader()` only scans `src/content/docs/` (hardcoded — see
// its `loaders.ts`), so pointing it at the corpus one directory up requires using
// Astro's underlying glob loader directly instead, with `base` set to the corpus
// root. `generateId` is overridden to be the literal `<category>/<file-stem>`
// path (no slugification) so a page's id is exactly its doc_id — the same
// contract the AI tool and the in-app route use, per the frozen spec:
// `docs/product/features/budgeting.md` -> doc_id `features/budgeting` -> this
// site's URL `/features/budgeting/`.
const docs = defineCollection({
  loader: glob({
    base: '../docs/product',
    pattern: '**/*.md',
    generateId: ({ entry }) => entry.replace(/\.md$/, ''),
  }),
  schema: docsSchema({
    // Extends (not replaces) Starlight's base frontmatter schema so the
    // corpus's extra required keys validate instead of being rejected as
    // unknown. `docsSchema`'s deep merge lets a key here override the base
    // schema's version of the same key (used for `description`, which
    // Starlight itself treats as optional but the corpus contract requires).
    extend: z.object({
      description: z.string().min(1, 'description is required'),
      category: z.enum([
        'getting-started',
        'concepts',
        'features',
        'lucius',
        'connectors',
        'operations',
        'troubleshooting',
        'reference',
      ]),
      order: z.number(),
      audience: z.array(z.enum(['user', 'operator', 'developer'])).min(1),
      status: z.enum(['stable', 'beta', 'coming-soon']),
      related: z.array(z.string()).optional().default([]),
      keywords: z.array(z.string()).optional().default([]),
    }),
  }),
});

export const collections = { docs };
