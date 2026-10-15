// pl-catmgr-mobile-cards.mjs — E2E regression for the mobile "Manage categories" fix
// (065734a17, frontend/src/components/tabs/plan/categoriesManager.tsx +
// CatMgrCard.tsx): CategoriesManager's `.pl-table-inner { min-width: 640px }` grid used to
// force horizontal scroll on a phone. Below 640px it now swaps CatMgrRow for CatMgrCard,
// stacking each category into 2 rows and dropping the grid header (the standalone "Hidden"
// toggle that used to make a 3rd row was removed as redundant with the kebab's own Hide/Unhide
// item — see CatMgrCard.tsx).
//
// The existing vitest unit test (categoriesManager.mobileCard.test.tsx) proves the render
// branch against a mocked matchMedia. This drives the REAL running app in a real Chromium
// browser at a real phone viewport: opens the modal through the actual mobile nav path (More
// -> Plan -> Plan settings -> Manage categories), measures actual layout geometry for
// horizontal overflow, confirms the DOM shape is cards-not-grid, exercises a card's own
// control end to end (a real POST + reload, not static markup), and then re-verifies the
// SAME code at a desktop viewport still renders the untouched grid.
import { mkdirSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { bootDisposableInstance } from './boot.mjs'

const outputDir = process.env.ONW_EVIDENCE || '/tmp/onw-catmgr-mobile-cards'
const port = Number(process.env.ONW_PORT || 8799)
mkdirSync(outputDir, { recursive: true })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const failures = []
function check(cond, message) {
  if (!cond) failures.push(message)
  console.log(`${cond ? 'PASS' : 'FAIL'} — ${message}`)
}

// bootDisposableInstance's own auto-seed path shells out to
// backend.scripts.seed_examples, which no longer exists on this branch (renamed to
// backend/scripts/seed_demo.py) — so, like pl-catmgr-table-scroll-touch.mjs and
// catfilter-in-modal.mjs (same modal, same harness), this boots against an empty external
// dataDir and seeds via POST /api/demo/seed instead.
//
// That external dataDir is deliberately named "data" one level under its OWN fresh mktemp
// root (not a bare mktemp leaf passed straight through) — backend/core/instance_lock.py's
// working-folder identity is DATA_DIR's PARENT, and only collapses to a shared root when
// DATA_DIR's own name is "data"; a bare mktemp leaf (e.g. "onw-foo-XXXXXX") makes its PARENT
// the system tmp dir itself, colliding with every other concurrent disposable instance on
// this machine that does the same. Keeping the "data" leaf keeps this run's working folder
// unique.
const dataRoot = mkdtempSync(join(tmpdir(), 'onw-catmgr-mobile-cards-'))
const dataDir = join(dataRoot, 'data')
const app = await bootDisposableInstance({ port, dataDir })
const browser = await chromium.launch({ headless: true })

try {
  const seedRes = await fetch(`${app.baseUrl}/api/demo/seed`, { method: 'POST' })
  if (!seedRes.ok) throw new Error(`demo seed failed: ${seedRes.status}`)

  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
  })
  const page = await context.newPage()
  const pageErrors = []
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  await page.goto(app.baseUrl, { waitUntil: 'domcontentloaded' })

  // Real mobile nav path to the modal — same as a phone user: bottom tab bar's More -> Plan,
  // then the page-level overflow menu ("Plan settings") -> "Manage categories" menu item.
  await page.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('button', { name: 'Plan', exact: true }).click()
  await page.waitForTimeout(400)
  await page.getByRole('button', { name: 'Plan settings' }).click()
  await page.getByRole('menuitem', { name: /Manage categories/i }).click()

  const table = page.locator('.pl-catmgr-table')
  await table.waitFor()
  // Rows arrive async after the modal mounts — wait for real content, not a fixed delay.
  await page.locator('.pl-catmgr-card:not(.pl-catmgr-addcard)').nth(3).waitFor({ timeout: 10_000 })
  await page.waitForTimeout(450)
  await page.screenshot({ path: join(outputDir, 'catmgr-mobile-cards.png') })

  // AC1 — no horizontal overflow, on the table region AND the document as a whole (the
  // regression: a fixed-width `.pl-table-inner` used to force both to scroll sideways).
  const tableMetrics = await table.evaluate((el) => ({
    scrollWidth: el.scrollWidth,
    clientWidth: el.clientWidth,
  }))
  check(
    tableMetrics.scrollWidth <= tableMetrics.clientWidth + 1,
    `.pl-catmgr-table has no horizontal overflow on a phone (scrollWidth ${tableMetrics.scrollWidth} <= clientWidth ${tableMetrics.clientWidth})`,
  )
  const docMetrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }))
  check(
    docMetrics.scrollWidth <= docMetrics.clientWidth + 1,
    `the document does not overflow horizontally either (scrollWidth ${docMetrics.scrollWidth} <= clientWidth ${docMetrics.clientWidth})`,
  )

  // AC2 — this is genuinely the card branch, not just "doesn't overflow": no grid header, no
  // grid rows, only CatMgrCard articles. A regression that quietly kept the grid but shrank it
  // with CSS would fail AC1 by luck but must fail here.
  const cardCount = await page.locator('.pl-catmgr-card:not(.pl-catmgr-addcard)').count()
  check(cardCount > 0, `at least one .pl-catmgr-card rendered (${cardCount})`)
  // Scoped to the modal's own table — the Plan/Budget page behind it renders an unrelated
  // `.pl-thead` for its own grid (budget.tsx), so an unscoped `.pl-thead` count is not a
  // reliable signal either way.
  check(
    (await page.locator('.pl-catmgr-table .pl-thead').count()) === 0,
    'no .pl-catmgr-table .pl-thead grid header rendered on mobile',
  )
  check(
    (await page.locator('.pl-catmgr-row:not(.pl-catmgr-addrow)').count()) === 0,
    'no .pl-catmgr-row grid rows rendered on mobile',
  )

  // AC3 (bonus) — a card's own control is live, not static markup. The standalone "Hidden"
  // switch that used to sit on the card face was removed (redundant — the kebab's own
  // Hide/Unhide item already calls the same onToggleHide handler), so this now opens the
  // first unlocked card's kebab and clicks Hide/Unhide there: confirms the real POST
  // /api/categories/{id}/(hide|unhide) fires and the list reloads with the flipped label,
  // then flips it back so the seeded data is left as found.
  const firstCard = page.locator('.pl-catmgr-card:not(.pl-catmgr-addcard)').first()
  const cardLabel = (await firstCard.locator('.pl-clabel').innerText()).trim()
  const kebabButton = page.getByRole('button', { name: `${cardLabel} options` })
  const hideMenuItem = page.getByRole('button', { name: /^(Hide|Unhide)$/ })
  const isHideMutation = (r) =>
    /\/api\/categories\/[^/]+\/(hide|unhide)$/.test(r.url()) && r.request().method() === 'POST'

  await kebabButton.click()
  const before = await hideMenuItem.innerText()
  await Promise.all([page.waitForResponse(isHideMutation), hideMenuItem.click()])

  await kebabButton.click()
  const after = await hideMenuItem.innerText()
  check(before !== after, `tapping a card's kebab Hide/Unhide actually mutated state (${before} -> ${after})`)
  // Flip it back so the run is idempotent if the evidence dir/data were ever reused.
  await Promise.all([page.waitForResponse(isHideMutation), hideMenuItem.click()])
  await page.keyboard.press('Escape')
  await sleep(400)

  check(pageErrors.length === 0, `no page errors on the mobile path (${pageErrors.length})`)
  if (pageErrors.length) console.log(pageErrors.slice(0, 5).join('\n'))

  // Desktop path untouched: close the modal, reload at a desktop viewport, reopen via the
  // desktop header button (not the mobile overflow menu), and confirm the ORIGINAL grid.
  await page.keyboard.press('Escape')
  await page.locator('.pl-catmgr-table').waitFor({ state: 'detached', timeout: 3000 }).catch(() => {})
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.evaluate(() => {
    localStorage.setItem('wealthTab', 'plan')
    localStorage.setItem('chat-open', 'false')
  })
  await page.reload({ waitUntil: 'domcontentloaded' })

  await page.getByRole('button', { name: 'Manage categories' }).click()
  const desktopTable = page.locator('.pl-catmgr-table')
  await desktopTable.waitFor()
  await page.locator('.pl-catmgr-row:not(.pl-catmgr-addrow)').nth(3).waitFor({ timeout: 10_000 })
  await page.waitForTimeout(450)
  await page.screenshot({ path: join(outputDir, 'catmgr-desktop-grid.png') })

  const theadCount = await page.locator('.pl-catmgr-table .pl-thead').count()
  check(theadCount === 1, `desktop still renders the .pl-catmgr-table .pl-thead grid header (count=${theadCount})`)
  check(
    (await page.locator('.pl-catmgr-row:not(.pl-catmgr-addrow)').count()) > 0,
    'desktop still renders .pl-catmgr-row grid rows',
  )
  check(
    (await page.locator('.pl-catmgr-card').count()) === 0,
    'desktop renders no .pl-catmgr-card at all — the mobile branch is not leaking into desktop',
  )

  await context.close()
} finally {
  await browser.close()
  app.stop()
}

console.log(`\nEvidence: ${outputDir}`)
if (failures.length > 0) {
  console.error(`\npl-catmgr-mobile-cards: FAIL (${failures.length})`)
  process.exit(1)
}
console.log('\npl-catmgr-mobile-cards: PASS')
