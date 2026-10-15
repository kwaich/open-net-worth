import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { CURATED_ACCENTS, DEFAULT_CUSTOM_HEX } from './widgetDisplayShared'

describe('curated widget accents', () => {
  it('keeps the Violet choice on the preserved violet token', () => {
    expect(CURATED_ACCENTS.find((accent) => accent.key === 'violet')).toEqual({
      key: 'violet',
      label: 'Violet',
      cssVar: 'var(--violet)',
    })
    expect(DEFAULT_CUSTOM_HEX).toBe('#7c6cf5')
  })

  it('resolves persisted violet accents to the preserved violet token', () => {
    const gridCard = readFileSync('src/components/tabs/overview/GridCard.tsx', 'utf8')
    const fullscreen = readFileSync('src/components/tabs/overview/WidgetFullscreen.tsx', 'utf8')

    expect(gridCard).toContain(
      "accent === 'amber' ? 'warn' : accent === 'violet' ? 'violet' : 'lucius'",
    )
    expect(fullscreen).toContain(
      "instance.accent === 'amber' ? 'warn' : instance.accent === 'violet' ? 'violet' : 'lucius'",
    )
  })
})
