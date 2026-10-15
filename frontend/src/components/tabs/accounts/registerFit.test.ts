import { describe, expect, it } from 'vitest'
import { REG_COLS } from './Register'
import { fitRegisterColumns } from './registerFit'

const widthsOf = () => Object.fromEntries(REG_COLS.map((c) => [c.key, c.w]))
const keys = (list: { key: string }[]) => list.map((c) => c.key)

describe('fitRegisterColumns', () => {
  it('leaves the columns alone before the container is measured', () => {
    const fit = fitRegisterColumns(REG_COLS, widthsOf(), 0)
    expect(keys(fit.cols)).toEqual(keys(REG_COLS))
    expect(fit.overflow).toBe(false)
    expect(fit.sourceShed).toBe(false)
  })

  it('keeps every column when the container is wide', () => {
    const fit = fitRegisterColumns(REG_COLS, widthsOf(), 1600)
    expect(keys(fit.cols)).toEqual(keys(REG_COLS))
    expect(fit.widths).toEqual(widthsOf())
    expect(fit.overflow).toBe(false)
  })

  it('sets Memo aside before squeezing Description at a laptop width', () => {
    const fit = fitRegisterColumns(REG_COLS, widthsOf(), 892)
    expect(keys(fit.cols)).not.toContain('memo')
    expect(keys(fit.cols)).toContain('source')
    expect(fit.overflow).toBe(false)
  })

  it('squeezes the other columns toward their minimums to keep Source in view', () => {
    const fit = fitRegisterColumns(REG_COLS, widthsOf(), 778)
    expect(keys(fit.cols)).toContain('source')
    expect(fit.widths.category).toBeLessThan(widthsOf().category!)
    expect(fit.widths.category).toBeGreaterThanOrEqual(104)
    expect(fit.overflow).toBe(false)
  })

  it('sets Source aside, then overflows, only when nothing else fits', () => {
    const tight = fitRegisterColumns(REG_COLS, widthsOf(), 700)
    expect(keys(tight.cols)).not.toContain('source')
    expect(tight.sourceShed).toBe(true)
    expect(tight.overflow).toBe(false)

    const tiny = fitRegisterColumns(REG_COLS, widthsOf(), 500)
    expect(tiny.overflow).toBe(true)
    expect(tiny.sourceShed).toBe(true)
  })

  it('never changes the column set the user chose, only what is shown', () => {
    const chosen = REG_COLS.filter((c) => c.key !== 'category' && !c.accountOnly)
    const fit = fitRegisterColumns(chosen, widthsOf(), 1600)
    expect(keys(fit.cols)).toEqual(keys(chosen))
  })
})
