// Mention/verb trigger detection — pure string scanners shared by the chat composer (live caret
// detection inside the contenteditable) and ChatPanel (re-exported for the existing unit tests).
//
// Both walk BACKWARD from the caret to the nearest trigger char, enforcing a word boundary: the
// trigger must sit at start-of-string or immediately after whitespace, so a mid-word "@" (emails)
// or "/" (URLs) never opens a picker.
//
// A separator is a regular space, a newline, OR a non-breaking space (U+00A0). The composer inserts
// an nbsp after each chip (a regular trailing space collapses in contenteditable), so "@" typed
// immediately after a chip must still count as boundary-preceded — otherwise the picker wouldn't
// open until you typed an extra real space. charCode 0xa0 is used so the source carries no invisible
// literal nbsp.
function isSep(c: string | undefined): boolean {
  return c === ' ' || c === '\n' || (c != null && c.charCodeAt(0) === 0xa0)
}

// ── @ mention token ────────────────────────────────────────────────────────────
// Spaces ARE allowed inside the query (multi-word entity names like "@whole foods"), but only up
// to MAX_AT_WORDS of them.
//
// The bound is load-bearing. "The picker auto-closes once a space-extended query stops matching"
// was the old theory, and it does not hold: `@Checking and then a whole sentence` still
// fuzzy-matches SOMETHING often enough that the picker stays armed, and an armed picker eats the
// Enter you meant as "send" — the owner's "I have to double tap a lot", 2026-08-01. No real entity
// name runs longer than this, so past it the caret is unambiguously in prose.
const MAX_AT_WORDS = 4

export function detectAtToken(
  value: string,
  caretPos: number,
): { atPos: number; query: string } | null {
  let i = caretPos - 1
  let words = 0
  while (i >= 0 && value[i] !== '\n') {
    if (value[i] === '@') {
      if (i === 0 || isSep(value[i - 1])) {
        return { atPos: i, query: value.slice(i + 1, caretPos) }
      }
      return null
    }
    // Count word gaps as we walk back; a run of spaces is one gap, not several.
    if (isSep(value[i]) && !isSep(value[i - 1])) {
      words++
      if (words >= MAX_AT_WORDS) return null
    }
    i--
  }
  return null
}

// ── / verb token ───────────────────────────────────────────────────────────────
// Same word-boundary rule, but a space CLOSES the token (verbs are single words). Mid-word "/"
// (e.g. URLs) is not a trigger.
export function detectSlashToken(
  value: string,
  caretPos: number,
): { slashPos: number; query: string } | null {
  let i = caretPos - 1
  while (i >= 0 && !isSep(value[i])) {
    if (value[i] === '/') {
      if (i === 0 || isSep(value[i - 1])) {
        return { slashPos: i, query: value.slice(i + 1, caretPos) }
      }
      return null
    }
    i--
  }
  return null
}

// ── Forward extension past the caret ─────────────────────────────────────────────
// detectAtToken/detectSlashToken only look BACKWARD from the caret, so their `query` is
// "everything typed so far up to the caret" — correct for detecting whether a trigger is
// active at all, but wrong as the actual search text once the caret sits INSIDE an
// already-typed word: arrow-key caret movement (no typing at all) then shrinks/changes the
// query on every keystroke, firing a genuinely different search each time (owner report
// 2026-08-11, "why moving cursor left and right trigger the search again?"). This finds
// where the CURRENT word actually ends — same separator rule, scanned forward instead of
// back — so a caller can extend the query (and the range it replaces on pick) to the whole
// word the caret is inside, not just the part before it.
export function extendQueryEnd(value: string, caretPos: number): number {
  let i = caretPos
  while (i < value.length && !isSep(value[i]) && value[i] !== '\n') i++
  return i
}
