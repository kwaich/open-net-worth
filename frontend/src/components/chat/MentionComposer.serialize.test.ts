// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { serializeSegments, readSegments } from './mentionComposerSerialize'
import type { ComposerSegment } from './mentionComposerSerialize'
import {
  hasMentionToken,
  serializeMentionRef,
  makeMentionTokenRE,
  type MentionRef,
} from '../../types/mentions'

const nw: MentionRef = { type: 'net_worth', ref: '', label: 'Net worth' }
const aapl: MentionRef = { type: 'holding', ref: 'pos_123', label: 'AAPL holding' }

describe('serializeMentionRef', () => {
  it('renders a [type: ref|label] token carrying the human label', () => {
    expect(serializeMentionRef(aapl)).toBe('[holding: pos_123|AAPL holding]')
  })
  it('renders an empty-ref singleton token with its label', () => {
    expect(serializeMentionRef(nw)).toBe('[net_worth: |Net worth]')
  })
})

describe('serializeSegments (inline chips ↔ tokens)', () => {
  it('interleaves chip tokens with text in DOM order', () => {
    const segs: ComposerSegment[] = [
      { kind: 'chip', mention: nw },
      { kind: 'text', text: ' merge with ' },
      { kind: 'chip', mention: aapl },
    ]
    const { text, mentions } = serializeSegments(segs)
    expect(text).toBe('[net_worth: |Net worth] merge with [holding: pos_123|AAPL holding]')
    // mentions come back in DOM order — the resolution authority on send
    expect(mentions).toEqual([nw, aapl])
  })
  it('chips only → just the token', () => {
    expect(serializeSegments([{ kind: 'chip', mention: aapl }]).text).toBe(
      '[holding: pos_123|AAPL holding]',
    )
  })
  it('text only → trimmed text, no mentions', () => {
    const { text, mentions } = serializeSegments([{ kind: 'text', text: '  plain question  ' }])
    expect(text).toBe('plain question')
    expect(mentions).toEqual([])
  })
  it('trims the outer edges but preserves text between chips', () => {
    const segs: ComposerSegment[] = [
      { kind: 'text', text: '  hello ' },
      { kind: 'chip', mention: nw },
      { kind: 'text', text: '  ' },
    ]
    expect(serializeSegments(segs).text).toBe('hello [net_worth: |Net worth]')
  })
})

describe('readSegments (contenteditable DOM walk)', () => {
  it('reads interleaved text + chip atoms in order; chip inner text never leaks', () => {
    const editor = document.createElement('div')
    editor.appendChild(document.createTextNode('look at '))
    const chip = document.createElement('span')
    chip.className = 'mention-chip'
    chip.dataset.chipType = 'holding'
    chip.dataset.chipRef = 'pos_123'
    chip.dataset.chipLabel = 'AAPL holding'
    chip.textContent = '◈AAPL holding×' // inner glyph/label/× must be ignored by the walk
    editor.appendChild(chip)
    editor.appendChild(document.createTextNode(' today'))

    const segs = readSegments(editor)
    expect(segs).toEqual([
      { kind: 'text', text: 'look at ' },
      { kind: 'chip', mention: { type: 'holding', ref: 'pos_123', label: 'AAPL holding' } },
      { kind: 'text', text: ' today' },
    ])
    expect(serializeSegments(segs).text).toBe('look at [holding: pos_123|AAPL holding] today')
  })

  it('maps a <br> to a newline', () => {
    const editor = document.createElement('div')
    editor.appendChild(document.createTextNode('line1'))
    editor.appendChild(document.createElement('br'))
    editor.appendChild(document.createTextNode('line2'))
    expect(serializeSegments(readSegments(editor)).text).toBe('line1\nline2')
  })
})

describe('makeMentionTokenRE (re-parse inline tokens — MessageBubble path)', () => {
  it('extracts every inline chip token back to type+ref+label', () => {
    const serialized = serializeSegments([
      { kind: 'chip', mention: nw },
      { kind: 'text', text: ' vs ' },
      { kind: 'chip', mention: aapl },
    ]).text
    const found: Array<{ type: string; ref: string; label: string }> = []
    const re = makeMentionTokenRE()
    let m: RegExpExecArray | null
    while ((m = re.exec(serialized)) !== null) {
      found.push({ type: m[1] ?? '', ref: (m[2] ?? '').trim(), label: (m[3] ?? '').trim() })
    }
    expect(found).toEqual([
      { type: 'net_worth', ref: '', label: 'Net worth' },
      { type: 'holding', ref: 'pos_123', label: 'AAPL holding' },
    ])
  })
})

describe('hasMentionToken (prefill detection)', () => {
  it('is true for an entity-ref prefill', () => {
    expect(hasMentionToken('[holding: pos_123] explain')).toBe(true)
  })
  it('is false for a plain free-text prefill', () => {
    expect(hasMentionToken('Walk me through my cash flow this quarter')).toBe(false)
  })
})
