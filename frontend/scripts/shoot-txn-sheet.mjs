// Screenshot + geometry verification for the transaction drawer's MOBILE BOTTOM SHEET (task 176).
//
//   node scripts/shoot-txn-sheet.mjs [--port 8095]
//
// Boots a QA-isolated disposable ONW instance (qa/boot.mjs — ONW_QA_MODE=1, DATA/CACHE/LOGS all
// in /tmp, NEVER :8080/live data) and opens a seeded transaction's detail drawer the way a user
// does — tapping its register row — at 390×844 (phone) and again at 1440×900 (desktop
// regression). The tab-level visual-qa.mjs sweep only visits top-level tabs and never opens
// this drawer, so the sheet needs its own lane.
//
// Asserts, on the phone pass (what "bottom sheet, not drawer" actually MEANS in geometry):
//   (a) the panel is full-bleed — its box spans the whole viewport width
//   (b) it is docked to the BOTTOM edge (bottom ≈ viewport height)
//   (c) it is capped at ~92dvh, so the page behind stays visible above it
//   (d) its top corners are rounded and the desktop col-resize handle is gone
//   (e) the footer's primary action clears the 44px tap-target minimum
//   (f) no page-level horizontal overflow, no console errors
// and on the desktop pass, that it is STILL a right-docked drawer (full height, ~384px wide).
//
// Requires: playwright devDependency + `npx playwright install chromium`. Dev tool, not
// CI-wired — mirrors shoot-news-sources.mjs's convention for a feature-specific lane.
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
const inst = await bootDisposableInstance({ port: Number(arg('port', '8095')) })

const failures = []
const check = (ok, label) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}`)
  if (!ok) failures.push(label)
}

/** Opens the first register row's detail drawer (the real user path: tap the row). */
async function openDrawer(page, label) {
  await page.locator('.acct-card, .acct-tr').first().waitFor({ state: 'visible', timeout: 20000 })
  await page.locator('.acct-card, .acct-tr').first().click({ timeout: 10000 })
  try {
    await page.locator('.acct-detail').waitFor({ state: 'visible', timeout: 10000 })
  } catch (err) {
    await page.screenshot({ path: `${outDir}/txn-sheet-${label}-FAILED.png` })
    throw err
  }
  await page.waitForTimeout(500)
}

let browser
try {
  browser = await chromium.launch()

  // ── phone: the bottom sheet ──────────────────────────────────────────────
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
  const page = await phone.newPage()
  page.on('pageerror', (err) => failures.push(`console error: ${err.message || err}`))
  await page.addInitScript(() => {
    localStorage.setItem('wealthTab', 'accounts')
    localStorage.setItem('chat-open', 'false')
  })
  await page.goto(`${inst.baseUrl}/`, { waitUntil: 'domcontentloaded', timeout: 20000 })
  await openDrawer(page, 'mobile')
  await page.screenshot({ path: `${outDir}/txn-sheet-mobile-view.png` })
  console.log(`screenshot -> ${outDir}/txn-sheet-mobile-view.png`)

  const geo = await page.evaluate(() => {
    const el = document.querySelector('.acct-detail')
    const r = el.getBoundingClientRect()
    const cs = getComputedStyle(el)
    const foot = document.querySelector('.acct-detail-edit')
    return {
      left: r.left, right: r.right, bottom: r.bottom, height: r.height,
      vw: window.innerWidth, vh: window.innerHeight,
      radiusTop: parseFloat(cs.borderTopLeftRadius), radiusBottom: parseFloat(cs.borderBottomLeftRadius),
      hasResize: !!document.querySelector('.acct-detail-resize'),
      hasGrab: !!document.querySelector('.acct-detail-grab'),
      footH: foot ? foot.getBoundingClientRect().height : 0,
      overflow: document.documentElement.scrollWidth - window.innerWidth,
    }
  })
  check(geo.left <= 1 && geo.right >= geo.vw - 1, `(a) full-bleed width (left ${geo.left}, right ${geo.right} of ${geo.vw})`)
  check(Math.abs(geo.bottom - geo.vh) <= 1, `(b) docked to the bottom edge (bottom ${geo.bottom} of ${geo.vh})`)
  check(geo.height <= geo.vh * 0.93 && geo.height > 200, `(c) capped near 92dvh (height ${Math.round(geo.height)} of ${geo.vh})`)
  check(geo.radiusTop >= 12 && geo.radiusBottom === 0, `(d1) top corners rounded, bottom square (${geo.radiusTop}/${geo.radiusBottom})`)
  check(!geo.hasResize && geo.hasGrab, `(d2) col-resize handle gone, grab handle present`)
  check(geo.footH >= 44, `(e) primary action ≥44px tap target (${Math.round(geo.footH)}px)`)
  check(geo.overflow <= 1, `(f) no page horizontal overflow (${geo.overflow}px)`)

  // The close target must survive a scroll — with a 92dvh sheet a header that scrolls away
  // is exactly the "can't get out of it" half of the original complaint.
  // A short viewport guarantees the body overflows regardless of which row the demo seed
  // hands us — without overflow there is no scroll and the assertion would prove nothing.
  await page.setViewportSize({ width: 390, height: 600 })
  await page.waitForTimeout(200)
  const scrolled = await page.evaluate(() => {
    const el = document.querySelector('.acct-detail')
    const offsets = () => {
      const sheet = el.getBoundingClientRect()
      return {
        x: document.querySelector('.acct-detail-x').getBoundingClientRect().top - sheet.top,
        grab: document.querySelector('.acct-detail-grab').getBoundingClientRect().top - sheet.top,
      }
    }
    const before = offsets()
    el.scrollTop = el.scrollHeight
    const after = offsets()
    return { top: el.scrollTop, before, after }
  })
  await page.screenshot({ path: `${outDir}/txn-sheet-mobile-scrolled.png` })
  console.log(`screenshot -> ${outDir}/txn-sheet-mobile-scrolled.png`)
  check(
    scrolled.top > 20 &&
      Math.abs(scrolled.after.x - scrolled.before.x) <= 1 &&
      Math.abs(scrolled.after.grab - scrolled.before.grab) <= 1,
    `(l) header (grab + ✕) stays pinned to the sheet top after scrolling ${Math.round(scrolled.top)}px to the end`,
  )
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(200)

  // Edit mode fits the sheet too (the form is the taller of the two bodies).
  await page.getByRole('button', { name: 'Edit transaction' }).click({ timeout: 10000 })
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${outDir}/txn-sheet-mobile-edit.png` })
  console.log(`screenshot -> ${outDir}/txn-sheet-mobile-edit.png`)
  const editGeo = await page.evaluate(() => {
    const r = document.querySelector('.acct-detail').getBoundingClientRect()
    return { bottom: r.bottom, height: r.height, vh: window.innerHeight, overflow: document.documentElement.scrollWidth - window.innerWidth }
  })
  check(Math.abs(editGeo.bottom - editGeo.vh) <= 1 && editGeo.height <= editGeo.vh * 0.93, `(g) edit mode stays a bottom sheet within 92dvh`)
  check(editGeo.overflow <= 1, `(h) edit mode: no page horizontal overflow (${editGeo.overflow}px)`)
  await phone.close()

  // ── desktop: unchanged right-docked drawer ───────────────────────────────
  const desk = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
  const dpage = await desk.newPage()
  dpage.on('pageerror', (err) => failures.push(`console error (desktop): ${err.message || err}`))
  await dpage.addInitScript(() => {
    localStorage.setItem('wealthTab', 'accounts')
    localStorage.setItem('chat-open', 'false')
  })
  await dpage.goto(`${inst.baseUrl}/`, { waitUntil: 'domcontentloaded', timeout: 20000 })
  await openDrawer(dpage, 'desktop')
  await dpage.screenshot({ path: `${outDir}/txn-sheet-desktop-view.png` })
  console.log(`screenshot -> ${outDir}/txn-sheet-desktop-view.png`)
  const dgeo = await dpage.evaluate(() => {
    const r = document.querySelector('.acct-detail').getBoundingClientRect()
    return {
      width: r.width, height: r.height, right: r.right,
      vw: window.innerWidth, vh: window.innerHeight,
      hasResize: !!document.querySelector('.acct-detail-resize'),
      hasGrab: !!document.querySelector('.acct-detail-grab'),
    }
  })
  check(Math.abs(dgeo.height - dgeo.vh) <= 1, `(i) desktop drawer still full height (${Math.round(dgeo.height)} of ${dgeo.vh})`)
  check(dgeo.width > 300 && dgeo.width < 800 && Math.abs(dgeo.right - dgeo.vw) <= 1, `(j) desktop drawer still right-docked at ${Math.round(dgeo.width)}px`)
  check(dgeo.hasResize && !dgeo.hasGrab, `(k) desktop keeps the resize handle and has no grab handle`)
  await desk.close()
} catch (err) {
  failures.push(`threw: ${err.message || err}`)
} finally {
  if (browser) await browser.close()
  inst.stop()
}

console.log('-'.repeat(70))
if (failures.length) {
  console.log(`${failures.length} FAILURE(S):`)
  for (const f of failures) console.log(`  - ${f}`)
  process.exit(1)
}
console.log('all checks passed')
