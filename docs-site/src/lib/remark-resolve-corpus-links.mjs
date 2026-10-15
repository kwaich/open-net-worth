// The corpus contract (docs/product, "Cross-links" section, corrected
// 2026-08-23) requires inline links to use a relative path with the `.md`
// extension, e.g. `[the ledger](../concepts/the-ledger.md)` — the one form
// that's resolvable by all three consumers (this site, the in-app /help
// viewer, and a human reading the raw file on GitHub).
//
// Astro's markdown pipeline does NOT rewrite `.md` links on its own — verified
// against a real build: left alone, `../concepts/the-ledger.md` renders as a
// literal `<a href="../concepts/the-ledger.md">`, which 404s (this site never
// ships the raw .md files, only the rendered /concepts/the-ledger/ route).
// This remark plugin rewrites any relative, `.md`-suffixed link to this
// site's actual route for that doc_id, preserving any `#anchor`.
import path from 'node:path';
import { visit } from 'unist-util-visit';
import { CORPUS_ROOT } from './corpus.mjs';

// A URL scheme (http:, mailto:, etc.) or a protocol-relative `//host/...`.
const EXTERNAL_URL = /^([a-z][a-z0-9+.-]*:|\/\/)/i;

export function remarkResolveCorpusLinks({ base = '/' } = {}) {
  const prefix = base === '/' ? '' : base.replace(/\/$/, '');

  return (tree, file) => {
    const sourcePath = file.path ?? file.history?.[0];
    if (!sourcePath) return;
    const sourceDir = path.dirname(sourcePath);

    visit(tree, 'link', (node) => {
      const url = node.url;
      if (!url || EXTERNAL_URL.test(url) || url.startsWith('#') || url.startsWith('mailto:')) {
        return;
      }
      const hashIndex = url.indexOf('#');
      const pathPart = hashIndex === -1 ? url : url.slice(0, hashIndex);
      const hash = hashIndex === -1 ? '' : url.slice(hashIndex);
      if (!pathPart.endsWith('.md')) return;

      const absoluteTarget = path.resolve(sourceDir, pathPart);
      const relativeToCorpus = path.relative(CORPUS_ROOT, absoluteTarget);
      if (relativeToCorpus.startsWith('..') || path.isAbsolute(relativeToCorpus)) {
        // Escapes docs/product entirely. A link to a real doc_id can never do
        // this — leave it as-is so it visibly 404s rather than guessing.
        return;
      }

      const docId = relativeToCorpus.slice(0, -'.md'.length).split(path.sep).join('/');
      node.url = `${prefix}/${docId}/${hash}`;
    });
  };
}
