import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * The asset page is one column: value, chart, then the asset's record — Ownership and Purchase
 * beside Details and Notes, where it is edited — then Value history. They used to sit in a fixed
 * 300px side rail (owner, 2026-09-27: "a lot of information … only shown as a side panel"),
 * then in a two-up grid of boxed cards whose rows forced each pair to one height (owner,
 * 2026-09-28: "ugly … when the information is not there and when it's even there"). `.as-body`
 * stays the register's two-column grid.
 */
const css = readFileSync(resolve(__dirname, './assets.css'), 'utf8')
const rule = (selector: string, source = css) =>
  new RegExp(`(^|\\n)\\s*${selector.replace(/\./g, '\\.')} \\{([^}]*)\\}`).exec(source)?.[2] ?? ''

describe('asset page layout', () => {
  it('lays the asset page out as one column', () => {
    expect(rule('.as-body.as-dbody')).toMatch(/grid-template-columns:\s*minmax\(0, 1fr\);/)
  })

  it('shows the record in two columns that keep their own heights — no boxes to match', () => {
    const cols = rule('.as-rec-cols')
    expect(cols).toMatch(/display:\s*grid;/)
    expect(cols).toMatch(/grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\);/)
    expect(cols).toMatch(/align-items:\s*start;/)
    expect(rule('.as-record')).toMatch(/container-type:\s*inline-size;/)
  })

  it('stacks them once the record is narrow — a phone, or a desktop beside the chat', () => {
    const narrow = css.slice(css.indexOf('@container (max-width: 600px)'))
    expect(rule('.as-rec-cols', narrow)).toMatch(/grid-template-columns:\s*minmax\(0, 1fr\);/)
  })

  it("keeps the register's own two-column body and rail untouched", () => {
    expect(rule('.as-body')).toMatch(/grid-template-columns:\s*minmax\(0, 1fr\) 300px;/)
    expect(rule('.as-rail')).toMatch(/position:\s*sticky;/)
  })
})

describe('phone value history rows', () => {
  it('are square — each row is a <button>, whose base style rounds it and bends its divider', () => {
    // Owner 2026-09-28: "why is the row curved?" — index.css gives every button 8px corners.
    expect(rule('.as-history-list-row')).toMatch(/border-radius:\s*0;/)
  })
})

describe('the asset page header on a phone', () => {
  const phone = css.slice(css.indexOf('@media (max-width: 640px)'))
  it('lets the name take the row, whatever sits before it — never by position', () => {
    // Owner 2026-09-28 ("wtf is wrong with the text padding and alignment?"): a rule on the
    // header's 2nd child hit the ⋯ once the photo became the page's cover above the row.
    expect(css).not.toMatch(/\.as-dhead > div:nth-child/)
    expect(rule('.as-dhead-title', phone)).toMatch(/min-width:\s*0;/)
    expect(rule('.as-dhead-actions', phone)).toMatch(/align-self:\s*flex-start;/)
    // A long name wraps inside its row; ⋯ never drops to a line of its own under it.
    expect(phone).toMatch(/\.as-dhead \{\s*flex-wrap:\s*nowrap;/)
    expect(rule('.as-dhead-title', phone)).toMatch(/flex:\s*1 1 0;/)
  })

  it('puts the figures and tabs on the same 16px edge as the name — the page body pads them', () => {
    expect(phone).not.toMatch(/\n\s*\.as-dstats,\n/)
    expect(phone).not.toMatch(/\.as-subtabs \{\s*margin-left: 16px;/)
    expect(phone).toMatch(/\n\s*\.as-dhead \{\s*margin-left: 16px;\s*margin-right: 16px;/)
  })
})

describe('the asset page on a wide screen', () => {
  it('reads at a comfortable width, centred — never edge to edge (owner, 2026-09-28)', () => {
    const page = rule('.as-dpage')
    const width = Number(/max-width:\s*(\d+)px;/.exec(page)?.[1])
    expect(width).toBeGreaterThanOrEqual(1000)
    expect(width).toBeLessThanOrEqual(1200)
    expect(page).toMatch(/margin:\s*0 auto;/)
  })

  it('lays a photo out as a media block: everything beside it starts at its top', () => {
    expect(rule('.as-dhead-media')).toMatch(/align-items:\s*flex-start;/)
    expect(rule('.as-dhead-sub')).toMatch(/max-width:\s*72ch;/)
  })
})
