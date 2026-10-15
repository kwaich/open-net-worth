// Typed handles onto the design-system CSS scales.
//
// The token unions and `var()` helpers are GENERATED from the single source of
// truth, src/design/tokens.source.mjs, into ./tokens.generated.ts (regenerated on
// every build via the `prebuild` hook). This module re-exports them so existing
// import sites keep working unchanged. Add/change token VALUES in tokens.source.mjs.
//
// RFC: .claude/rfcs/frontend-styling-pwa-ideal.md (Phase A — token pipeline)
export * from './tokens.generated'
