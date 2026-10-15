// The corpus contract (docs/product) requires every file to open with exactly
// one `# Title` matching its frontmatter `title` — load-bearing for the other
// two consumers, which render raw markdown with no separate title chrome
// (ai/tools/docs.py's read_doc, and the in-app /help route). Starlight, on the
// other hand, already renders `frontmatter.title` as the page's H1 via
// PageTitle.astro (which is also where the coming-soon/beta status badge
// lives). Left alone, a corpus page would show the title twice. This strips
// only a leading, first-node H1 from the rendered body — never a `#` that
// shows up later in the document, and never anything in files outside the
// docs collection (src/pages/index.astro isn't markdown, so it's unaffected).
export function remarkStripLeadingH1() {
  return (tree) => {
    const first = tree.children[0];
    if (first && first.type === 'heading' && first.depth === 1) {
      tree.children.shift();
    }
  };
}
