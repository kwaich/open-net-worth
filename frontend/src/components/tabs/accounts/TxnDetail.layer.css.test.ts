import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * The transaction drawer opened OVER a modal (the review cards, the import modal) sits AT the
 * modal tier, never above it. Every one of them portals to <body>, so at one z-index the one
 * opened later paints later: the drawer lands over the modal it came from, and a dialog opened
 * FROM the drawer — the receipt viewer, the statement viewer, the delete confirm — lands over the
 * drawer. Lifted to --z-toast, the drawer covered all of those instead (owner report 2026-09-26:
 * a receipt tapped in review mode opened invisibly behind it). jsdom cannot compute the cascade,
 * so this guards the CSS text (ADR 2026-09-03-phone-sheet-opened-from-inside-a-modal).
 */
const accountsCss = readFileSync(resolve(__dirname, './accounts.css'), 'utf8')
const indexCss = readFileSync(resolve(__dirname, '../../../index.css'), 'utf8')

/** The z-index of the FIRST rule whose selector list is exactly `selector`. */
function zIndexOf(css: string, selector: string): string | undefined {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const rule = css.match(new RegExp(`(?:^|[}\\n])\\s*${escaped}\\s*\\{([^}]*)\\}`))
  expect(rule, `no rule for ${selector}`).toBeTruthy()
  return rule?.[1]?.match(/z-index:\s*([^;]+);/)?.[1]?.trim()
}

describe('the transaction drawer opened over a modal', () => {
  it('stacks at the same tier as a Modal, so opening order decides who is on top', () => {
    const modal = zIndexOf(indexCss, '.modal-overlay')
    expect(modal).toBe('var(--z-modal)')
    expect(zIndexOf(accountsCss, '.acct-detail-wrap.above-modal')).toBe(modal)
  })
})
