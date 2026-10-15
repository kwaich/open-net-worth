import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * Two class-card layout facts the summary cards depend on.
 *
 * `.as-card-brow-lbl` had no rule of its own — it only LOOKED styled because `.as-card-brow`
 * sets a colour its children inherit. `npm run lint:classes` accepts a class defined anywhere,
 * so a stem match passes the gate while the label is one refactor from losing its tone.
 *
 * `.as-grid` matches every card in a row to the tallest neighbour (e.g. a two-row Investments
 * breakdown) — a single-kind card (Retirement: headline only) grows a band of empty surface
 * BELOW its own content, never between its title and its value.
 */
const css = readFileSync(resolve(__dirname, './assets.css'), 'utf8')

describe('assets class cards', () => {
  it('gives the breakdown-row label its own rule', () => {
    expect(css).toMatch(/^\.as-card-brow-lbl \{[^}]*color:/m)
  })

  it('matches every card in a row to the row height', () => {
    const grid = /^\.as-grid \{[^}]*\}/m.exec(css)?.[0] ?? ''
    expect(grid).toMatch(/align-items:\s*stretch;/)
  })
})
