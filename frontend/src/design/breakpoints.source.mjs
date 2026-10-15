// ─────────────────────────────────────────────────────────────────────────────
// BREAKPOINT SOURCE OF TRUTH
//
// Single source for the responsive-layout breakpoints. Consumed ONLY by the
// codegen `frontend/scripts/build-tokens.mjs`, which emits:
//
//   • src/index.breakpoints.generated.css  — @custom-media definitions
//
// Also re-exported by `src/components/ui/breakpoints.ts` for the typed JS
// runtime (useBreakpoint hook, legacy BP constant).
//
// Nothing imports THIS file at runtime. Edit values here, then run
// `npm run build:tokens` (also runs automatically on `npm run build` via prebuild).
//
// RFC: .claude/rfcs/phaseD-responsive.md  (Phase D — Responsive System)
// ─────────────────────────────────────────────────────────────────────────────

/** The 4-step min-width breakpoint ladder (px integers). */
export const BP_SOURCE = {
  /** phone → small tablet: folds 360/420/480/560/639/640 legacy boundaries */
  sm: 640,
  /** tablet portrait: folds 720/760 legacy boundaries */
  md: 768,
  /** tablet landscape → desktop: folds 900/1020/1023/1024 legacy boundaries */
  lg: 1024,
  /** wide desktop: reserved for future headroom; zero current consumers */
  xl: 1280,
}
