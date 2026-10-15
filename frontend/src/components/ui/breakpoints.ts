// Breakpoint tokens — the SINGLE typed runtime source for the layout breakpoints.
//
// The canonical integer values are defined in src/design/breakpoints.source.mjs and
// consumed by scripts/build-tokens.mjs which generates the @custom-media CSS file.
// This .ts file is the typed runtime handle for JS consumers (useBreakpoint hook, etc).
// Drift between this file and breakpoints.source.mjs is caught by the CI test
// src/__tests__/breakpoints-codegen.test.ts — no "MUST match" comment needed.
//
// RFC: .claude/rfcs/phaseD-responsive.md  (Phase D — D-1)

/** The 4-step min-width breakpoint ladder (px integers).
 *
 *   sm   640 — phone → small tablet
 *   md   768 — tablet portrait
 *   lg  1024 — tablet landscape → desktop
 *   xl  1280 — wide desktop (reserved; zero current consumers)
 */
export const BP = {
  sm: 640,
  md: 768,
  lg: 1024,
  xl: 1280,
} as const

export type Breakpoint = 'sm' | 'md' | 'lg' | 'xl'
