// capture_import_choose_read.mjs — production captures of the Statement Import CHOOSE and
// READ steps, ported verbatim from the handoff prototype's `flow3.jsx` DropStep / ReadStep.
//
// Boots the DEV-ONLY story harness (frontend/qa/import-story) on a high port with NO api
// proxy — every prop is a stub, so this can never touch live data.
//
//   node qa/browser-use/capture_import_choose_read.mjs
//
// Output: qa/artifacts/statement-import-parity/choose-read-*.png
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

const fixture = path.join(os.tmpdir(), 'onw-story-choose.csv')
await writeFile(fixture, 'Date,Description,Amount\n2026-06-01,MoMo top-up,-200000\n')

const PORT = 5198
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
const sheet = page => page.locator('.ui-modal-card').first()

try {
  for (const theme of ['dark', 'light']) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 })
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)) })
    page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 300)))
    const shot = async (name) => {
      await page.waitForTimeout(220)
      await sheet(page).screenshot({ path: path.join(out, `choose-read-${name}-${theme}.png`) })
      console.log(`  wrote choose-read-${name}-${theme}.png`)
    }
    const go = async (query) => {
      await page.goto(`http://localhost:${PORT}/qa/import-story/?theme=${theme}${query}`, { waitUntil: 'load' })
      await page.locator('.imp3-drop').waitFor({ timeout: 30_000 })
    }

    // 1 — Choose, nothing picked: the centred dropzone + the URL link as its own control.
    await go('')
    await shot('1-drop')
    await page.getByRole('button', { name: 'or paste a URL / Google Sheet link' }).click()
    await shot('2-drop-url')

    // 1b — Choose, picked via URL. The peek fingerprints file BYTES, so a hosted source is
    // `unavailable`, not `unknown`: it keeps the standalone "Re-read from scratch" control,
    // which is the only thing that sets force_relearn.
    await page.getByLabel('Statement URL').fill('https://docs.google.com/spreadsheets/d/e/2PACX/pub?output=csv')
    await page.getByRole('button', { name: 'Load' }).click()
    await page.locator('.imp3-file').waitFor({ timeout: 30_000 })
    await shot('2b-url-source')

    // 2 — Choose, picked, UNKNOWN source (one account chip, from defaultAccountId).
    await go('')
    await page.locator('.imp-file-input').setInputFiles(fixture)
    await page.locator('.imp3-file').waitFor({ timeout: 30_000 })
    await shot('3-picked-unknown')

    // 3 — Choose, picked, no account chosen: no stand-in chip, CTA disabled until one is
    // picked (2026-07-27: the auto-detect chip this used to capture was reverted — an account
    // is always explicit, matching the prototype's own `disabled={acctIds.length === 0}`).
    await go('&acct=none')
    await page.locator('.imp-file-input').setInputFiles(fixture)
    await page.locator('.imp3-file').waitFor({ timeout: 30_000 })
    await shot('4-no-account')
    // the account picker, open
    await page.getByRole('button', { name: 'Select account' }).click()
    await page.locator('.imp3-lpick-pop').waitFor({ timeout: 10_000 })
    await shot('5-account-picker')

    // 4 — Choose, KNOWN source: the "I know this source." banner, saved instructions, and a
    // second account chip (a statement covering several wallets).
    await go('&peek=known')
    await page.locator('.imp-file-input').setInputFiles(fixture)
    await page.locator('.imp3-known').waitFor({ timeout: 30_000 })
    await page.getByRole('button', { name: '+ Add account' }).click()
    await page.getByRole('option', { name: /MoMo Wallet/ }).click()
    await shot('6-known-multi-account')
    // the cache hint: editing remembered instructions re-processes the file
    await page.locator('.imp3-cfg-note textarea').fill('Rows with MOMO are transfers. Also: skip the FX fee lines.')
    await page.locator('.imp3-cache-hint').first().waitFor({ timeout: 10_000 })
    await shot('7-instructions-changed')

    // 5 — Read: the pulsing orb + the rotating phase line (`slow=1` holds the step open).
    await go('&peek=known&slow=1')
    await page.locator('.imp-file-input').setInputFiles(fixture)
    await page.locator('.imp3-known').waitFor({ timeout: 30_000 })
    await page.getByRole('button', { name: /Read the statement/ }).click()
    await page.locator('.imp3-reading').waitFor({ timeout: 10_000 })
    await shot('8-reading')
    await page.waitForTimeout(1200)
    await shot('8b-reading-later')

    // narrow pass — the handoff has no mobile spec, so this only proves nothing clips.
    await go('&peek=known')
    await page.setViewportSize({ width: 390, height: 844 })
    await page.locator('.imp-file-input').setInputFiles(fixture)
    await page.locator('.imp3-known').waitFor({ timeout: 30_000 })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    if (overflow > 1) errors.push(`horizontal overflow ${overflow}px at 390px (${theme})`)
    await shot('9-mobile')
    await page.close()
  }
} finally {
  await browser.close()
  stop()
}

if (errors.length) { console.error('FAIL\n' + errors.join('\n')); process.exit(1) }
console.log('PASS — choose/read captures written to', out)
