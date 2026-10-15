// A reply's markdown fence that opens a code block nobody meant to draw.
//
// Persona QA 2026-10-14: a streamed answer rendered as an empty code box and ONE monospace line.
// The text was "```You have S$788 left…\n\nThose are the only amounts…" — a stray fence glued to
// the first paragraph. Markdown reads everything after the backticks as the fence's INFO STRING,
// so the paragraph vanished into a `language-…` class and the next one became code. An unclosed
// fence swallows the rest of the message the same way, and while a reply streams a fence that
// will close later looks identical to one that never does, so the damage is done before the
// stream can say which it was.
//
// This repairs only the LAST, still-open fence, and only when it cannot be real code: its "info"
// is a sentence rather than a language tag, or it is a bare fence with nothing before or nothing
// after it. A language-tagged fence, or a bare one with prose before and code after, is left to
// stream as the code it is. A balanced message is never touched.

const OPENER = /^[ \t]{0,3}`{3,}([^`]*)$/
const BARE = /^[ \t]{0,3}`{3,}[ \t]*$/
const LANGUAGE = /^[\w+#.-]{1,24}$/

const blank = (line: string) => !line.trim()

export function repairStrayFences(text: string): string {
  if (!text.includes('```')) return text
  const lines = text.split('\n')
  let open = -1
  lines.forEach((line, i) => {
    if (open >= 0) {
      if (BARE.test(line)) open = -1
    } else if (OPENER.test(line)) {
      open = i
    }
  })
  if (open < 0) return text

  const info = (OPENER.exec(lines[open] ?? '')?.[1] ?? '').trim()
  if (info && !LANGUAGE.test(info)) {
    lines[open] = info
  } else if (!info && (lines.slice(0, open).every(blank) || lines.slice(open + 1).every(blank))) {
    lines.splice(open, 1)
  } else {
    return text
  }
  return lines.join('\n').replace(/^\n+/, '')
}
