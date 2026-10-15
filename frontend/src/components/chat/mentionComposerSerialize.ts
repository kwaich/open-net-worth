// Pure serialization for the chat composer — kept out of MentionComposer.tsx so that file only
// exports the component (fast-refresh / react-refresh hygiene).
//
// serializeSegments turns the ordered segment list into the inline "[type: ref|label]" transcript
// string AND the structured MentionRef[] (the backend's resolution authority, in DOM order).
// readSegments walks a contenteditable's direct children into that segment list.

import type { MentionRef, MentionType } from '../../types/mentions'
import { serializeMentionRef } from '../../types/mentions'

// One ordered piece of composer content.
export type ComposerSegment = { kind: 'text'; text: string } | { kind: 'chip'; mention: MentionRef }

export function serializeSegments(segments: ComposerSegment[]): {
  text: string
  mentions: MentionRef[]
} {
  const mentions: MentionRef[] = []
  let out = ''
  for (const seg of segments) {
    if (seg.kind === 'chip') {
      mentions.push(seg.mention)
      out += serializeMentionRef(seg.mention)
    } else {
      out += seg.text
    }
  }
  return { text: out.trim(), mentions }
}

// Walk a contenteditable's direct children into the ordered segment list. Exported for testing
// against a hand-built DOM (jsdom's Selection API is too thin to drive the live caret path).
export function readSegments(editor: HTMLElement): ComposerSegment[] {
  const segs: ComposerSegment[] = []
  editor.childNodes.forEach((node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      segs.push({ kind: 'text', text: node.textContent ?? '' })
    } else if (node instanceof HTMLElement) {
      if (node.classList.contains('mention-chip')) {
        const d = node.dataset
        segs.push({
          kind: 'chip',
          mention: {
            type: (d.chipType ?? '') as MentionType,
            ref: d.chipRef ?? '',
            label: d.chipLabel ?? '',
            // Recover the presentation meta buildChipEl stamped, so it survives the
            // serialize() → composerDraft → seed() hand-off (chip keeps its price/amount/color).
            ...(d.chipDate ? { chipDate: d.chipDate } : {}),
            ...(d.chipAmt ? { chipAmt: d.chipAmt } : {}),
            ...(d.chipAmtNeg != null ? { chipAmtNeg: d.chipAmtNeg === 'true' } : {}),
            ...(d.chipColor ? { color: d.chipColor } : {}),
          },
        })
      } else if (node.tagName === 'BR') {
        segs.push({ kind: 'text', text: '\n' })
      } else {
        // Stray wrapper (e.g. a <div> a browser injected on Enter) — flatten its text.
        segs.push({ kind: 'text', text: node.textContent ?? '' })
      }
    }
  })
  return segs
}
