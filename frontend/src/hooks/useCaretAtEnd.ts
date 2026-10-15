import { useRef, type FocusEvent } from 'react'

type TextControl = HTMLInputElement | HTMLTextAreaElement

/** An onFocus for a field opened to edit what is already in it: the FIRST time it takes focus
 *  the caret goes after the last character, and a long text scrolls to its end. A field
 *  focused in code (a dialog's autofocus) gets its caret before the first character on iOS
 *  (owner: "why the cursor is at the beginning?"). Later focuses are the user's own taps, and
 *  keep the caret where the tap put it. */
export function useCaretAtEnd(): (e: FocusEvent<TextControl>) => void {
  const placed = useRef(false)
  return (e) => {
    if (placed.current) return
    placed.current = true
    const el = e.currentTarget
    // After the browser has placed the caret for this focus itself.
    requestAnimationFrame(() => {
      if (document.activeElement !== el) return
      const end = el.value.length
      el.setSelectionRange(end, end)
      el.scrollTop = el.scrollHeight
    })
  }
}
