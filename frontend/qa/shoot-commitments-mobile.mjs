// Mobile screenshot + geometry probe for the Commitments tab and a commitment DETAIL screen.
//
//   node qa/shoot-commitments-mobile.mjs [--out-dir /tmp/onw-commitments-mobile] [--w 390]
//
// Why this exists: scripts/visual-qa.mjs only visits TAB routes, so the commitment detail
// screen — where the horizontal-overflow and timeline-strip defects actually live — was never
// covered by `make ui-gate`. This boots the same disposable instance, opens the first
// commitment, and reports real measured geometry (overflow, tap-target heights, chart box)
// alongside screenshots, so a mobile claim can be checked against pixels instead of CSS
// reasoning.
//
// DEV tool: never imported by the app. Hang-safe hard timeout; always tears the backend down.
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { bootDisposableInstance } from './boot.mjs'

const arg = (name, def = null) => {
  const i = process.argv.indexOf(`--${name}`)
  if (i === -1) return def
  const next = process.argv[i + 1]
  return next && !next.startsWith('--') ? next : true
}

const outDir = String(arg('out-dir', '/tmp/onw-commitments-mobile'))
const width = Number(arg('w', '390'))
const height = Number(arg('h', '844'))

mkdirSync(outDir, { recursive: true })

const HARD_TIMEOUT_MS = 180000
const killer = setTimeout(() => {
  console.log('TIMEOUT — exiting')
  process.exit(1)
}, HARD_TIMEOUT_MS)

const { baseUrl, stop } = await bootDisposableInstance({ port: 8767 })
const browser = await chromium.launch()
const rows = []

try {
  // isMobile+hasTouch so `(hover: none)` / `(pointer: coarse)` queries resolve the way they do
  // on a real phone — the whole point of the touch-reachability work.
  const ctx = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  })
  // The SPA is served ONLY at "/" — deep paths 404 to a plain error page (which has no
  // viewport meta, so a naive probe there reports innerWidth 980 and measures nothing real).
  // Seed the active tab the way scripts/visual-qa.mjs does.
  await ctx.addInitScript(() => {
    localStorage.setItem('wealthTab', 'commitments')
    localStorage.setItem('chat-open', 'false')
  })
  const page = await ctx.newPage()
  const errors = []
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

  const probe = async (label) => {
    const m = await page.evaluate(() => {
      const de = document.documentElement
      const el = (s) => document.querySelector(s)
      const box = (s) => {
        const n = el(s)
        if (!n) return null
        const r = n.getBoundingClientRect()
        return { w: Math.round(r.width), h: Math.round(r.height) }
      }
      // Widest element that pokes past the viewport — names the actual overflow culprit.
      let worst = null
      for (const n of document.querySelectorAll('body *')) {
        const r = n.getBoundingClientRect()
        if (r.width === 0 && r.height === 0) continue
        const over = Math.round(r.right - window.innerWidth)
        if (over > 1 && (!worst || over > worst.over)) {
          worst = { over, tag: n.tagName.toLowerCase(), cls: String(n.className).slice(0, 60) }
        }
      }
      return {
        scrollW: de.scrollWidth,
        innerW: window.innerWidth,
        worst,
        chartSvg: box('.c2-chart-svg'),
        chartPlot: box('.c2-chart-plot'),
        seg: box('.c2-seg'),
        sort: box('.c2-select--sort'),
        hz: box('.c3-hz button'),
        needBtn: box('.c2-need-btns .ui-btn'),
        thead: box('.c2-thead'),
        strip: box('.c2-strip'),
        stripList: box('.c2-strip-list'),
        dettab: box('.c3-dettabs button'),
      }
    })
    rows.push({ label, ...m })
    return m
  }

  // domcontentloaded, not networkidle: the SPA holds long-lived SSE/polling connections.
  await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded', timeout: 20000 })
  await page.waitForTimeout(1800)
  await page.screenshot({ path: `${outDir}/commitments-list-${width}.png`, fullPage: true })
  await probe('list')

  // Open the first commitment row → the detail screen.
  const row = page.locator('.c2-row').first()
  if (await row.count()) {
    await row.click()
    await page.waitForTimeout(1200)
    await page.screenshot({ path: `${outDir}/commitments-detail-${width}.png`, fullPage: true })
    await probe('detail')
  } else {
    console.log('!! no .c2-row found — detail not captured')
  }

  console.log(`\n=== geometry @ ${width}px (isMobile, hasTouch) ===`)
  for (const r of rows) {
    const ov = r.scrollW - r.innerW
    console.log(
      `\n[${r.label}] scrollWidth=${r.scrollW} innerWidth=${r.innerW} overflow=${ov > 1 ? `!! +${ov}px` : 'none'}`,
    )
    if (r.worst) console.log(`   widest overhang: +${r.worst.over}px  <${r.worst.tag} class="${r.worst.cls}">`)
    for (const k of ['chartSvg', 'chartPlot', 'seg', 'sort', 'hz', 'needBtn', 'thead', 'strip', 'stripList', 'dettab']) {
      if (r[k]) console.log(`   ${k.padEnd(10)} ${r[k].w}×${r[k].h}`)
    }
  }
  if (errors.length) console.log(`\n!! console errors: ${errors.length}\n   ${errors.slice(0, 5).join('\n   ')}`)
  console.log(`\nscreenshots → ${outDir}`)
} finally {
  await browser.close()
  stop()
  clearTimeout(killer)
}
