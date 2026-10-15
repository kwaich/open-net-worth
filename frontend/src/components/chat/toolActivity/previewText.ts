/**
 * Reasoning as one flat line of prose, for the collapsed preview.
 *
 * The preview is a raw string in a clamped span — only the EXPANDED body goes through
 * ReactMarkdown — so a Codex summary, which arrives wrapped as `**Planning onboarding
 * profile saving**`, showed its asterisks (owner report, 2026-08-24). Rendering markdown
 * inside a two-line clamp is worse than it sounds (block elements, their own margins, a
 * heading's font size), so the preview STRIPS the syntax instead of honouring it.
 *
 * Deliberately not a markdown parser: it removes the emphasis, heading and code markers that
 * actually appear in reasoning summaries and leaves everything else exactly as written. A
 * stray asterisk survives as an asterisk, which is the honest outcome for a preview.
 */
// The backtick character (U+0060) appears here only via \u0060 escapes, never literally:
// a raw backtick in this file reads to frontend/scripts/check-css-classes.mjs's naive
// template-literal scanner as an unmatched delimiter, desyncing dead-CSS-class detection
// for every file that follows this one in its traversal order (owner, 2026-08-25).
const CODE_FENCE_RE = /\u0060\u0060\u0060+[\s\S]*?\u0060\u0060\u0060+/g
const INLINE_CODE_RE = /\u0060([^\u0060]+)\u0060/g

export function previewText(md: string): string {
  return (
    md
      .replace(CODE_FENCE_RE, ' ')
      .replace(INLINE_CODE_RE, '$1')
      .replace(/^\s{0,3}#{1,6}\s+/gm, '')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/__([^_]+)__/g, '$1')
      .replace(/(^|[\s([{])[*_]([^*_\n]+)[*_](?=[\s.,;:!?)\]}]|$)/g, '$1$2')
      // One line of prose: the clamp gives it two, and blank lines would waste one of them.
      .replace(/\s*\n+\s*/g, ' ')
      .trim()
  )
}
