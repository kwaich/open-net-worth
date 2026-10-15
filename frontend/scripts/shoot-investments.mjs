// Screenshot harness for the Investments tab visual-verification gate.
//
// Two modes:
//   node scripts/shoot-investments.mjs --proto
//       Spins up a minimal static HTTP server for the prototype directory,
//       renders the prototype per sub-tab, saves /tmp/proto-shots/<tab>.png.
//
//   node scripts/shoot-investments.mjs --app [--port 8093] [--base-url http://…]
//       Boots a QA-isolated disposable ONW instance (ONW_QA_MODE=1, DATA/CACHE/LOGS
//       all in /tmp — NEVER :8080 or live data) and captures the live built
//       Investments tab per sub-tab to /tmp/build-shots/<tab>.png.
//       If the app isn't built yet (frontend/dist absent) it builds it first.
//
// Viewport: 1440×900, 2× device pixel ratio.
//
// Requires: playwright devDependency + `npx playwright install chromium` (run once).
// This is a dev tool; the app never imports it.
//
// SAFETY: --app never touches :8080/5174 or ~/.open-net-worth. bootDisposableInstance
// in frontend/qa/boot.mjs enforces this with assertSafeBoot() — throws before
// spawning anything if those invariants are violated.

import { chromium } from 'playwright'
import { mkdirSync, existsSync, createReadStream, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, extname } from 'node:path'
import { createServer } from 'node:http'
import { once } from 'node:events'

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

// ── CLI args ────────────────────────────────────────────────────────────────

const argv = process.argv.slice(2)
const hasFlag = (f) => argv.includes(f)
const argVal = (f, def = null) => {
  const i = argv.indexOf(f)
  if (i === -1) return def
  const next = argv[i + 1]
  return next && !next.startsWith('--') ? next : def
}

const MODE_PROTO = hasFlag('--proto')
const MODE_APP = hasFlag('--app')

if (!MODE_PROTO && !MODE_APP) {
  console.error('Usage:')
  console.error('  node scripts/shoot-investments.mjs --proto')
  console.error('  node scripts/shoot-investments.mjs --app [--port 8093] [--base-url http://…] [--data-dir /path/to/data]')
  process.exit(1)
}

// Sub-tabs defined in the prototype's <PageTabs> list (in order).
const SUBTABS = [
  { key: 'holdings', label: 'Holdings' },
  { key: 'performance', label: 'Performance' },
  { key: 'income', label: 'Income' },
  { key: 'activity', label: 'Activity' },
  { key: 'options', label: 'Options' },
  { key: 'equity', label: 'Equity Compensation' },
]

const VIEWPORT = { width: 1440, height: 900 }
const HARD_TIMEOUT_MS = 180_000

// ── Minimal static HTTP server for the prototype ─────────────────────────────
// file:// blocks XHR (CORS null origin) for sub-resources loaded by Babel.
// A plain HTTP server on 127.0.0.1 sidesteps that completely.

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.mjs': 'application/javascript; charset=utf-8',
  '.jsx': 'text/javascript; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
}

function serveStatic(root) {
  const server = createServer((req, res) => {
    // Only GET; strip query strings.
    const rawPath = decodeURIComponent(req.url.split('?')[0])
    const target = rawPath === '/' ? '/Investment Tab.html' : rawPath
    const filePath = join(root, target)
    const mime = MIME[extname(filePath).toLowerCase()] || 'application/octet-stream'
    try {
      const body = readFileSync(filePath)
      res.writeHead(200, { 'Content-Type': mime, 'Cache-Control': 'no-store' })
      res.end(body)
    } catch {
      res.writeHead(404)
      res.end('Not found')
    }
  })
  return server
}

async function listenOnFreePort(server) {
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return `http://127.0.0.1:${server.address().port}`
}

// ── Prototype mode ───────────────────────────────────────────────────────────
//
// Serve the prototype directory over HTTP so Babel can load tweaks-panel.jsx
// without hitting the file:// CORS restriction, then click each sub-tab.

async function shootProto(browser, outDir) {
  const protoDir = join(
    REPO_ROOT,
    'frontend',
    'design_handoffs',
    'investments_2',
    'prototype',
  )

  if (!existsSync(join(protoDir, 'Investment Tab.html'))) {
    throw new Error(`Prototype not found in ${protoDir}`)
  }

  const server = serveStatic(protoDir)
  const baseUrl = await listenOnFreePort(server)
  console.log(`Prototype served at ${baseUrl}`)

  const results = []
  try {
    for (const { key, label } of SUBTABS) {
      const ctx = await browser.newContext({
        viewport: VIEWPORT,
        deviceScaleFactor: 2,
      })
      const page = await ctx.newPage()

      try {
        // networkidle waits for the React+Babel CDN scripts and the tweaks-panel
        // local fetch to all complete before we touch the DOM.
        await page.goto(baseUrl, { waitUntil: 'networkidle', timeout: 60_000 })
        // Babel compiles synchronously but React useEffect / state setup runs
        // async — extra settle ensures the app is fully mounted.
        await page.waitForTimeout(2500)

        if (key !== 'holdings') {
          const tabBtn = page.getByRole('tab', { name: label, exact: true })
          await tabBtn.waitFor({ state: 'visible', timeout: 10_000 })
          await tabBtn.click()
          await page.waitForTimeout(800)
        }

        const outPath = join(outDir, `${key}.png`)
        await page.screenshot({ path: outPath, fullPage: false })
        console.log(`SHOT [proto] ${key} → ${outPath}`)
        results.push({ key, ok: true })
      } catch (e) {
        console.error(`ERROR [proto] ${key}: ${e.message}`)
        results.push({ key, ok: false, error: e.message })
      } finally {
        await ctx.close()
      }
    }
  } finally {
    server.close()
  }
  return results
}

// ── Live-app mode ────────────────────────────────────────────────────────────
//
// Boots a QA-isolated throwaway ONW instance via bootDisposableInstance()
// (frontend/qa/boot.mjs). That harness:
//   - mkdtemp DATA_DIR/CACHE_DIR/LOGS_DIR, asserts they're not live
//   - sets ONW_QA_MODE=1, DEMO_MODE=1, AUTH_DISABLED=1
//   - seeds demo data, spawns backend on the given port (NEVER 8080)
//
// We navigate to /investments (via localStorage) then click each sub-tab.

async function shootApp(browser, baseUrl, outDir) {
  const results = []

  for (const { key, label } of SUBTABS) {
    const ctx = await browser.newContext({
      viewport: VIEWPORT,
      deviceScaleFactor: 2,
    })
    await ctx.addInitScript(() => {
      localStorage.setItem('wealthTab', 'investments')
      localStorage.setItem('chat-open', 'false')
      // Show amounts (disable the privacy mask) so fidelity shots show real figures.
      localStorage.setItem('wealthHide', '0')
    })
    const page = await ctx.newPage()

    try {
      await page.goto(baseUrl + '/', { waitUntil: 'domcontentloaded', timeout: 30_000 })
      // Longer settle: charts, sparklines and the returns/income stat cards fetch
      // async — too short a wait captures loading skeletons instead of data.
      await page.waitForTimeout(3000)

      if (key !== 'holdings') {
        const tabBtn = page.getByRole('tab', { name: label, exact: true })
        await tabBtn.waitFor({ state: 'visible', timeout: 10_000 })
        await tabBtn.click()
        await page.waitForTimeout(2200)
      }

      const outPath = join(outDir, `${key}.png`)
      await page.screenshot({ path: outPath, fullPage: false })
      console.log(`SHOT [app] ${key} → ${outPath}`)
      results.push({ key, ok: true })
    } catch (e) {
      console.error(`ERROR [app] ${key}: ${e.message}`)
      results.push({ key, ok: false, error: e.message })
    } finally {
      await ctx.close()
    }
  }
  return results
}

// ── Main ─────────────────────────────────────────────────────────────────────

let _teardown = () => {}
const killer = setTimeout(() => {
  console.log('TIMEOUT — exiting (hard 180s cap)')
  try { _teardown() } catch { /* ignore */ }
  process.exit(1)
}, HARD_TIMEOUT_MS)

const protoOutDir = '/tmp/proto-shots'
const buildOutDir = '/tmp/build-shots'

let browser
let exitCode = 0

try {
  browser = await chromium.launch()

  if (MODE_PROTO) {
    mkdirSync(protoOutDir, { recursive: true })
    console.log(`Shooting prototype → ${protoOutDir}`)
    const results = await shootProto(browser, protoOutDir)
    const failed = results.filter((r) => !r.ok)
    if (failed.length) {
      console.error(`\n${failed.length} sub-tab(s) failed:`, failed.map((r) => r.key).join(', '))
      exitCode = 1
    } else {
      console.log(`\nAll ${results.length} prototype shots saved to ${protoOutDir}`)
    }
  }

  if (MODE_APP) {
    const explicitBaseUrl = argVal('--base-url')
    const explicitDataDir = argVal('--data-dir')
    const port = Number(argVal('--port', '8093'))

    let baseUrl
    if (explicitBaseUrl) {
      baseUrl = explicitBaseUrl.replace(/\/$/, '')
      console.log(`Using provided base URL: ${baseUrl}`)
    } else {
      const { bootDisposableInstance } = await import('../qa/boot.mjs')
      console.log(`Booting disposable QA instance on port ${port}…`)
      const inst = await bootDisposableInstance({ port, dataDir: explicitDataDir, log: console.log })
      _teardown = inst.stop
      baseUrl = inst.baseUrl
    }

    mkdirSync(buildOutDir, { recursive: true })
    console.log(`Shooting live app → ${buildOutDir}`)
    const results = await shootApp(browser, baseUrl, buildOutDir)
    const failed = results.filter((r) => !r.ok)
    if (failed.length) {
      console.error(`\n${failed.length} sub-tab(s) failed:`, failed.map((r) => r.key).join(', '))
      exitCode = 1
    } else {
      console.log(`\nAll ${results.length} app shots saved to ${buildOutDir}`)
    }
  }
} catch (e) {
  console.error('FATAL:', e.message)
  exitCode = 1
} finally {
  if (browser) await browser.close()
  clearTimeout(killer)
  try { _teardown() } catch { /* ignore */ }
}

process.exit(exitCode)
