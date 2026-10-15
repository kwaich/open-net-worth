import { describe, expect, it } from 'vitest'

import { CONFIG_TABS, PRIMARY_TABS, TAB_LABELS, TAB_SUBTITLES, TABS } from './tabs'

describe('TABS', () => {
  it('has no duplicate entries', () => {
    // A duplicate slug means two sidebar entries and two route matches for one screen.
    expect(new Set(TABS).size).toBe(TABS.length)
  })

  it('every tab carries a label and a subtitle', () => {
    for (const tab of TABS) {
      expect(TAB_LABELS[tab], `${tab} label`).toBeTruthy()
      expect(TAB_SUBTITLES[tab], `${tab} subtitle`).toBeTruthy()
    }
  })

  it('every navigable tab is a real tab', () => {
    for (const tab of [...PRIMARY_TABS, ...CONFIG_TABS]) {
      expect(TABS, `${tab} in TABS`).toContain(tab)
    }
  })

  it('Lucius is the first tab and the first primary nav destination', () => {
    expect(TABS[0]).toBe('lucius')
    expect(PRIMARY_TABS[0]).toBe('lucius')
  })
})
