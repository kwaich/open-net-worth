// The Accounts master pane is navigation on the page canvas, not a raised card.
// It must share the page background in every theme; using --surface paints the
// whole resizable column as a contrasting slab.

import { readFileSync } from 'node:fs'
import { chromium } from 'playwright'

const css = [
  new URL('../src/index.tokens.generated.css', import.meta.url),
  new URL('../src/components/tabs/accounts/accounts.css', import.meta.url),
]
  .map((url) => readFileSync(url, 'utf8'))
  .join('\n')

const expected = {
  dark: 'rgb(14, 16, 20)',
  warm: 'rgb(237, 234, 227)',
  light: 'rgb(255, 255, 255)',
}

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })

try {
  await page.setContent(`<style>${css}</style><aside class="acct-side">Accounts</aside>`)
  const results = {}
  for (const [theme, wanted] of Object.entries(expected)) {
    const actual = await page.evaluate((name) => {
      document.documentElement.dataset.theme = name
      return getComputedStyle(document.querySelector('.acct-side')).backgroundColor
    }, theme)
    results[theme] = actual
    if (actual !== wanted) {
      throw new Error(
        `Accounts sidebar must match the ${theme} page canvas ${wanted}, got ${actual}`,
      )
    }
  }
  console.log(JSON.stringify(results, null, 2))
} finally {
  await browser.close()
}
