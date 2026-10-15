// Screenshot verification for the INT9 Phase C News-sources panel.
//
//   node scripts/shoot-news-sources.mjs [--port 8094]
//
// Boots a QA-isolated disposable ONW instance (qa/boot.mjs — ONW_QA_MODE=1, DATA/CACHE/LOGS
// all in /tmp, NEVER :8080/live data), drives INTO the Monitors workspace modal (the tab-level
// visual-qa.mjs sweep only visits top-level tabs, never opens this modal), opens the "News
// sources" rail item, and captures desktop + mobile screenshots — asserting the mobile view has
// no PAGE-level horizontal overflow (the sources table scrolls inside its own
// `.intel-src-table-scroll` wrapper instead, per the UI spec).
//
// Requires: playwright devDependency + `npx playwright install chromium`. Dev tool, not
// CI-wired — mirrors shoot-investments.mjs's convention for a feature-specific screenshot lane.
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const arg = (name, def = null) => {
  const i = process.argv.indexOf(`--${name}`)
  if (i === -1) return def
  const next = process.argv[i + 1]
  return next && !next.startsWith('--') ? next : def
}

const outDir = String(arg('out-dir', '/tmp/onw-visual-qa'))
mkdirSync(outDir, { recursive: true })

const { bootDisposableInstance } = await import('../qa/boot.mjs')
const inst = await bootDisposableInstance({ port: Number(arg('port', '8094')) })

let browser
let failures = []
try {
  browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  page.on('pageerror', (err) => failures.push(`console error: ${err.message || err}`))

  await page.addInitScript(() => {
    localStorage.setItem('wealthTab', 'intel')
    localStorage.setItem('chat-open', 'false')
  })
  await page.goto(inst.baseUrl + '/', { waitUntil: 'domcontentloaded', timeout: 20000 })
  await page.waitForTimeout(1200)

  // Open the Monitors workspace (the "Watchlists" header button, title "Manage all
  // watchlists & monitors" — WatchlistsSection.tsx), then the "News sources" rail item.
  await page.locator('[title="Manage all watchlists & monitors"]').first().click({ timeout: 10000 })
  await page.waitForTimeout(300)
  await page.getByText('News sources', { exact: true }).click({ timeout: 10000 })
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${outDir}/news-sources-panel.png`, fullPage: true })
  console.log(`screenshot -> ${outDir}/news-sources-panel.png (desktop)`)

  // A builtin row's actions cell must stay empty — no delete affordance (SRCP-17).
  const builtinActionCell = await page.evaluate(() => {
    const rows = document.querySelectorAll('.intel-src-table-scroll tbody tr')
    const row = Array.from(rows).find((r) => r.children.length === 6)
    return row ? row.children[5].textContent?.trim() : null
  })
  if (builtinActionCell) failures.push(`a builtin row's actions cell is non-empty: "${builtinActionCell}"`)

  // Mobile width — the table must scroll inside its own wrapper, never the whole page (SRCP-18).
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${outDir}/news-sources-panel-mobile.png`, fullPage: true })
  console.log(`screenshot -> ${outDir}/news-sources-panel-mobile.png (mobile)`)

  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }))
  if (overflow.scrollWidth > overflow.innerWidth + 1) {
    failures.push(`page h-overflow at 390px: scrollWidth ${overflow.scrollWidth} > innerWidth ${overflow.innerWidth}`)
  }
} finally {
  await browser?.close()
  await inst.stop()
}

if (failures.length) {
  console.log(`FAIL — ${failures.length} issue(s):`)
  for (const f of failures) console.log(`  - ${f}`)
  process.exitCode = 1
} else {
  console.log('PASS — no page-level overflow at 390px, builtin rows carry no delete affordance.')
}
