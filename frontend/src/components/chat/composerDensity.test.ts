import { describe, expect, it } from 'vitest'
import { composerDensity } from './composerDensity'

describe('composerDensity', () => {
  it('shows the whole cluster on a wide panel', () => {
    expect(composerDensity(600)).toEqual({ showCost: true, showEffortLabel: true, showModel: true })
  })

  it('drops the cost figure first — the default 400px panel never shows it', () => {
    expect(composerDensity(479).showCost).toBe(false)
    expect(composerDensity(400)).toEqual({
      showCost: false,
      showEffortLabel: false,
      showModel: true,
    })
  })

  it('drops the effort label next, keeping the model picker', () => {
    expect(composerDensity(439)).toMatchObject({ showEffortLabel: false, showModel: true })
    expect(composerDensity(440)).toMatchObject({ showEffortLabel: true })
  })

  it('drops the model picker last, at the old crammed range', () => {
    expect(composerDensity(359).showModel).toBe(false)
    expect(composerDensity(360).showModel).toBe(true)
  })

  it('sheds strictly in order — never a later part without the earlier ones', () => {
    for (let w = 300; w <= 700; w += 1) {
      const d = composerDensity(w)
      if (d.showCost) expect(d.showEffortLabel && d.showModel).toBe(true)
      if (d.showEffortLabel) expect(d.showModel).toBe(true)
    }
  })
})
