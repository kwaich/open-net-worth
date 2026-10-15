import { chromium } from '../../frontend/node_modules/playwright/index.mjs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { mkdir } from 'node:fs/promises'

const here = path.dirname(fileURLToPath(import.meta.url))
const out = path.join(here, 'artifacts', 'statement-import-parity')
await mkdir(out, { recursive: true })

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
await page.goto('http://localhost:4179/prototype/Statement%20Import%20v3.html', {
  waitUntil: 'domcontentloaded',
})
await page.getByRole('button', { name: /Drop a statement here/ }).click()
await page.getByRole('button', { name: /Read the statement/ }).click()
await page.locator('.imp3-review').waitFor({ timeout: 15_000 })

const states = [
  { width: 1440, height: 1000, label: '1440' },
  { width: 1024, height: 900, label: '1024' },
  { width: 390, height: 844, label: 'mobile' },
]

for (const theme of ['dark', 'light']) {
  await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme)
  for (const state of states) {
    await page.setViewportSize({ width: state.width, height: state.height })
    for (const dock of ['closed', 'open']) {
      await page.screenshot({ path: path.join(out, `prototype-${state.label}-${theme}-lucius-${dock}.png`) })
    }
  }
}

await browser.close()
