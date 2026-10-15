// capture_import_production.mjs — production captures of the Statement Import Decide step,
// at the SAME states/widths as capture_import_reference.mjs, so the two sets can be diffed.
//
// Boots the DEV-ONLY story harness (frontend/qa/import-story) on a high port with NO api
// proxy — the fixture preview means zero network calls, so this can never touch live data.
//
//   node qa/browser-use/capture_import_production.mjs
//
// Output: qa/artifacts/statement-import-parity/production-*.png
import { chromium } from '../../frontend/node_modules/playwright/index.mjs'
import { spawn } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontend = path.resolve(here, '../../frontend')
const out = path.resolve(here, '../artifacts/statement-import-parity')
await mkdir(out, { recursive: true })

// generated at capture time into the OS temp dir — never an artefact worth committing
const fixture = path.join(os.tmpdir(), 'onw-story-statement.csv')
await writeFile(fixture, 'Date,Description,Amount\n2026-06-01,MoMo top-up,-200000\n')

const PORT = 5199
const vite = spawn(path.join(frontend, 'node_modules/.bin/vite'), ['--port', String(PORT), '--strictPort'], {
  cwd: frontend, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, VITE_DEV_API_TARGET: 'http://127.0.0.1:1' },
})
let viteLog = ''
vite.stdout.on('data', d => { viteLog += d })
vite.stderr.on('data', d => { viteLog += d })
const stop = () => { try { vite.kill('SIGTERM') } catch { /* already gone */ } }
process.on('exit', stop)

const deadline = Date.now() + 60_000
while (Date.now() < deadline && !/Local:.*:\d+/.test(viteLog)) await new Promise(r => setTimeout(r, 250))
if (!/Local:.*:\d+/.test(viteLog)) { console.error(viteLog); stop(); process.exit(1) }

const errors = []
const browser = await chromium.launch({ headless: true })
try {
  for (const theme of ['dark', 'light']) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 2 })
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)) })
    page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 300)))
    await page.goto(`http://localhost:${PORT}/qa/import-story/?theme=${theme}`, { waitUntil: 'load' })

    // Drive the REAL flow: pick a file → Read the statement → the stubbed preview lands.
    await page.locator('.imp-file-input').setInputFiles(fixture)
    await page.getByRole('button', { name: /Read the statement/ }).click()
    await page.locator('.imp3-review-list, .imp3-scroll').first().waitFor({ timeout: 30_000 })
    await page.waitForTimeout(700)

    const shot = async (name) => {
      await page.waitForTimeout(220)
      await page.locator('.ui-modal-card, .imp3-sheet').first().screenshot({ path: path.join(out, `production-${name}-${theme}.png`) })
      console.log(`  wrote production-${name}-${theme}.png`)
    }

    await shot('1440-all')
    await page.locator('.imp3-arow-line').nth(1).click()
    await shot('1440-row-open')
    const needs = page.getByRole('button', { name: /^Needs you/ })
    if (await needs.count()) {
      await needs.first().click()
      await shot('1440-needs-you')
      const choice = page.locator('.imp3-choice').first()
      if (await choice.count()) { await choice.click(); await shot('1440-needs-you-decided') }
    }
    const leftOut = page.getByRole('button', { name: /^Left out/ })
    if (await leftOut.count()) { await leftOut.first().click(); await shot('1440-left-out') }
    const notOn = page.getByRole('button', { name: /^Not on statement/ })
    if (await notOn.count()) { await notOn.first().click(); await shot('1440-ledger-only') }
    await page.getByRole('button', { name: /^All/ }).first().click()
    for (const [label, width, height] of [['1024', 1024, 900], ['mobile', 390, 844]]) {
      await page.setViewportSize({ width, height })
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
      if (overflow > 1) errors.push(`horizontal overflow ${overflow}px at ${width}px (${theme})`)
      await shot(`${label}-all`)
    }
    await page.setViewportSize({ width: 1440, height: 1100 })
    await page.getByRole('button', { name: /Confirm & book/ }).click()
    await page.locator('.imp3-done').waitFor({ timeout: 15_000 })
    await shot('1440-booked')
    await page.close()
  }
} finally {
  await browser.close()
  stop()
}

if (errors.length) { console.error('\nFAIL:\n' + errors.map(e => '  - ' + e).join('\n')); process.exit(1) }
console.log('\nproduction captures →', out, '\nno console errors, no horizontal overflow')
