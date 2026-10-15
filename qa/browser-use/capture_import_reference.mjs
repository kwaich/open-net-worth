// capture_import_reference.mjs — reference captures of the Statement Import handoff prototype.
// Serves frontend/design_handoffs/statement_import_handoff/prototype over http (the prototype
// loads React/Babel from a CDN and its own relative stylesheets), drives the Decide step, and
// writes one PNG per state × width so the production build can be diffed against it.
//
//   node qa/browser-use/capture_import_reference.mjs
//
// Output: qa/artifacts/statement-import-parity/reference-*.png
import { chromium } from '../../frontend/node_modules/playwright/index.mjs'
import http from 'node:http'
import { readFile } from 'node:fs/promises'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '../../frontend/design_handoffs/statement_import_handoff/prototype')
const out = path.resolve(here, '../artifacts/statement-import-parity')
await mkdir(out, { recursive: true })

const MIME = { '.html': 'text/html', '.css': 'text/css', '.jsx': 'text/babel', '.js': 'text/javascript', '.png': 'image/png' }
const server = http.createServer(async (req, res) => {
  try {
    const rel = decodeURIComponent((req.url || '/').split('?')[0])
    const file = path.join(root, rel === '/' ? 'Statement Import v3.html' : rel)
    if (!file.startsWith(root)) { res.writeHead(403).end(); return }
    const body = await readFile(file)
    res.writeHead(200, { 'content-type': MIME[path.extname(file)] || 'application/octet-stream' })
    res.end(body)
  } catch { res.writeHead(404).end('not found') }
})
await new Promise(r => server.listen(4179, r))

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 2 })
page.on('console', m => { if (m.type() === 'error') console.log('  console error:', m.text().slice(0, 200)) })

await page.goto('http://localhost:4179/Statement%20Import%20v3.html', { waitUntil: 'load' })
// The prototype boots via babel-standalone; wait for the file picker it opens on.
await page.locator('.imp3-drop').waitFor({ timeout: 30_000 })
await page.locator('.imp3-drop').click()
await page.locator('.imp3-lubtn').click()
await page.locator('.imp3-review').waitFor({ timeout: 30_000 })
await page.waitForTimeout(600)

const shot = async (name) => {
  await page.waitForTimeout(220)
  await page.locator('.imp3-sheet').screenshot({ path: path.join(out, `reference-${name}.png`) })
  console.log('  wrote reference-' + name + '.png')
}

// 1440 — the composition width the handoff was designed at.
await shot('1440-all')
// Expand the first matched row so the disclosure body (Cmp + seg + After) is captured.
await page.locator('.imp3-arow-line').first().click()
await shot('1440-row-open')
// Needs-you queue (persistent cards, 1/2/3 keys).
await page.getByRole('button', { name: /^Needs you/ }).click()
await shot('1440-needs-you')
await page.locator('.imp3-choice').first().click()
await shot('1440-needs-you-decided')
// Left out filter.
await page.getByRole('button', { name: /^Left out/ }).click()
await shot('1440-left-out')
// Not-on-statement (ledger-only) disclosures.
await page.getByRole('button', { name: /^Not on statement/ }).click()
await shot('1440-ledger-only')
// Back to All, then narrower widths.
await page.getByRole('button', { name: /^All$/ }).click()
for (const [label, width, height] of [['1024', 1024, 900], ['mobile', 390, 844]]) {
  await page.setViewportSize({ width, height })
  await shot(`${label}-all`)
}
await page.setViewportSize({ width: 1440, height: 1100 })
// Booked confirmation (Mark all as reviewed).
await page.locator('.imp3-commit').click()
await page.locator('.imp3-done').waitFor({ timeout: 10_000 })
await shot('1440-booked')

await browser.close()
server.close()
console.log('reference captures →', out)
