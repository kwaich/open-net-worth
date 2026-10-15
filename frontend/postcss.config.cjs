// PostCSS config — Phase D-1 (RFC: .claude/rfcs/phaseD-responsive.md)
// Enables @custom-media so the responsive breakpoint tokens (--sm/--md/--lg/--xl)
// and their parity aliases (--below-sm/--below-md/--below-lg/--sm-only) can be
// used in @media conditions. The definitions live in
// src/index.breakpoints.generated.css (generated from src/design/breakpoints.source.mjs).
// Vite auto-discovers this file at the project root (frontend/).
//
// `:hover` rules are gated behind `@media (hover: hover)` directly in the source CSS (a
// one-time codemod — see git history for "disable :hover on touch devices"), NOT via a
// live postcss-hover-media-feature plugin here: that plugin's per-selector-list processing
// made `vite build` hang for 20+ minutes across this app's many code-split CSS chunks, so
// the transform is applied once to the source instead of on every build.
module.exports = {
  plugins: {
    'postcss-custom-media': {},
  },
}
