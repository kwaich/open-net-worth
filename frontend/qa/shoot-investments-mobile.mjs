// Screenshot evidence for the Investments mobile-density change.
//
//   node qa/shoot-investments-mobile.mjs [--out-dir /tmp/onw-inv-mobile]
//
// Boots the SAME hardened disposable instance ui-gate uses (frontend/qa/boot.mjs:
// tmp DATA_DIR/CACHE_DIR/LOGS_DIR, ONW_QA_MODE, never :8080, never live data) and
// shoots the Holdings sub-tab at phone widths.
//
// Pass --data-dir "$(make qa-data)" to drive a CLONE of the real book (secrets
// stripped, isolated tmp dir). The bundled demo fixtures carry positions but no
// prices, so their book values to zero and the tab renders EmptyHoldings — which
// is why a plain ui-gate run never exercises this layout at all.
//
// Spec: docs/superpowers/specs/2026-07-30-investments-mobile-density-design.md
// Acceptance: docs/qa/acceptance/investments-mobile.md
//
// DEV tool — the app never imports it. Hang-safe: hard timeout, always tears down.

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { bootDisposableInstance } from './boot.mjs'

const arg = (name, def = null) => {
  const i = process.argv.indexOf(`--${name}`)
  if (i === -1) return def
  const next = process.argv[i + 1]
  return next && !next.startsWith('--') ? next : true
}

const outDir = String(arg('out-dir', '/tmp/onw-inv-mobile'))
const dataDir = arg('data-dir') ? String(arg('data-dir')) : undefined
const PORT = 8094
const WIDTHS = [
  { label: 'phone-390', w: 390, h: 1400 },
  { label: 'phone-360', w: 360, h: 1400 },
]

const hardExit = setTimeout(() => {
  console.error('TIMEOUT — forcing exit')
  process.exit(99)
}, 240_000)
hardExit.unref?.()

mkdirSync(outDir, { recursive: true })

let stop = () => {}
let browser
try {
  const boot = await bootDisposableInstance({ port: PORT, dataDir })
  stop = boot.stop
  const baseUrl = boot.baseUrl

  // What does the book actually contain on this instance?
  const portfolio = await (await fetch(`${baseUrl}/api/portfolio`)).json()
  const rows = portfolio.rows ?? []
  const invRows = rows.filter((r) => r.position_id || r.instrument_id)
  console.log(`[shoot] /api/portfolio → ${rows.length} rows, ${invRows.length} investment rows`)
  for (const r of invRows) {
    console.log(
      `[shoot]   ${r.label ?? r.symbol ?? r.ticker} qty=${r.quantity} price=${r.price} base=${r._base}`,
    )
  }

  browser = await chromium.launch()
  for (const { label, w, h } of WIDTHS) {
    const ctx = await browser.newContext({
      viewport: { width: w, height: h },
      deviceScaleFactor: 2,
      colorScheme: 'dark',
    })
    const page = await ctx.newPage()
    const errors = []
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

    // Land on Investments → Holdings the way the app persists tab state.
    await page.addInitScript(() => {
      localStorage.setItem('wealthTab', 'investments')
      localStorage.setItem('inv-subtab', 'holdings')
      // Prove the phone gate ignores a saved TABLE preference (acceptance C6).
      localStorage.setItem('inv-holdings-density', 'table')
    })
    await page.goto(`${baseUrl}/investments?sub=holdings`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2500)

    const shot = `${outDir}/investments-${label}.png`
    await page.screenshot({ path: shot, fullPage: true })
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    )
    // Name the culprits: every element whose box actually extends past the viewport.
    // A parent that merely CONTAINS an overflowing child is noise, so report the
    // deepest offenders (those with no offending descendant) first.
    const culprits = await page.evaluate(() => {
      const vw = window.innerWidth
      const bad = [...document.querySelectorAll('body *')].filter((el) => {
        const r = el.getBoundingClientRect()
        return r.width > 0 && r.right > vw + 1
      })
      const set = new Set(bad)
      return bad
        .filter((el) => ![...el.querySelectorAll('*')].some((c) => set.has(c)))
        .slice(0, 12)
        .map((el) => {
          const r = el.getBoundingClientRect()
          const cs = getComputedStyle(el)
          return {
            sel: el.tagName.toLowerCase() + (el.className && typeof el.className === 'string'
              ? '.' + el.className.trim().split(/\s+/).join('.') : ''),
            right: Math.round(r.right),
            width: Math.round(r.width),
            minWidth: cs.minWidth,
            text: (el.textContent || '').trim().slice(0, 40),
          }
        })
    })
    // Exact border-to-border gaps between the toolbar controls, plus whether the
    // grouping control still paints a chevron. Both were reported by eye; measure them.
    const toolbar = await page.evaluate(() => {
      const bar = document.querySelector('.mk-toolbar')
      if (!bar) return null
      const kids = [...bar.children].filter((el) => getComputedStyle(el).display !== 'none')
      const boxes = kids.map((el) => {
        const r = el.getBoundingClientRect()
        const cs = getComputedStyle(el)
        return {
          sel: el.className.trim().split(/\s+/)[0],
          left: Math.round(r.left),
          right: Math.round(r.right),
          w: Math.round(r.width),
          h: Math.round(r.height),
          font: cs.fontSize,
        }
      })
      const gaps = boxes.slice(1).map((b, i) => Math.round(b.left - boxes[i].right))
      const group = bar.querySelector('.mk-toolsel--group')
      const gcs = group && getComputedStyle(group)
      return {
        boxes,
        gaps,
        gapProp: getComputedStyle(bar).columnGap,
        groupChevron: gcs ? gcs.backgroundImage : 'no-group-control',
        groupTextIndent: gcs ? gcs.textIndent : null,
      }
    })
    const seen = await page.evaluate(() => ({
      cells: document.querySelectorAll('.mk-sum-cell').length,
      solo: !!document.querySelector('.mk-sum--solo'),
      meta: document.querySelector('.mk-sum-meta')?.textContent?.trim() ?? null,
      chipRow: !!document.querySelector('.intel-feed-bar'),
      density: !!document.querySelector('.mk-density'),
      classSelect: !!document.querySelector('[aria-label="Filter by asset class"]'),
      cards: document.querySelectorAll('.mk-hold').length,
      table: !!document.querySelector('.mk-tbl'),
      firstCard: document.querySelector('.mk-hold-inner')?.innerText?.replace(/\n/g, ' | ') ?? null,
    }))
    console.log(`[shoot] ${label} → ${shot}`)
    console.log(`[shoot]   overflowPx=${overflow} consoleErrors=${errors.length}`)
    console.log(`[shoot]   ${JSON.stringify(seen, null, 1)}`)
    console.log(`[shoot]   toolbar ${JSON.stringify(toolbar, null, 1)}`)
    if (culprits.length) console.log(`[shoot]   OVERFLOW ${JSON.stringify(culprits, null, 1)}`)
    await ctx.close()
  }
} finally {
  await browser?.close()
  stop()
  clearTimeout(hardExit)
}
