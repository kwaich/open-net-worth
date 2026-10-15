// catfilter-in-modal.mjs — real-Chromium verification of the phone Kind-filter sheet opened
// from INSIDE the Manage categories PlanningModal (owner report 2026-09-03): the sheet and its
// scrim actually receive taps (a Radix modal switches body pointer events off), the scrim paints
// OVER the parent modal, and dismissing the filter — scrim tap, X, a pick, a drag — never takes
// the parent sheet with it. Rows read label · count · tick, not "All39".
import { mkdirSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { bootDisposableInstance } from './boot.mjs'

const outputDir = process.env.ONW_EVIDENCE || '/tmp/onw-catfilter-in-modal'
// 8796 belongs to pl-catmgr-table-scroll-touch.mjs (same modal); two lanes may run both.
const port = Number(process.env.ONW_PORT || 8798)
mkdirSync(outputDir, { recursive: true })
const dataDir = mkdtempSync(join(tmpdir(), 'onw-catfilter-data-'))
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const failures = []
const pageErrors = []
function check(cond, message) {
  if (!cond) failures.push(message)
  console.log(`${cond ? 'PASS' : 'FAIL'} — ${message}`)
}

const app = await bootDisposableInstance({ port, dataDir })
const browser = await chromium.launch({ headless: true })
try {
  await fetch(`${app.baseUrl}/api/demo/seed`, { method: 'POST' })
  const context = await browser.newContext({
    viewport: { width: 390, height: 760 },
    hasTouch: true,
    isMobile: true,
  })
  const page = await context.newPage()
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  await page.goto(app.baseUrl, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('button', { name: 'Plan', exact: true }).click()
  await sleep(400)

  const parentOpen = () => page.locator('.pl-modal').count()
  const filterOpen = () => page.locator('.kebab-sheet').count()
  const openManager = async () => {
    if (await parentOpen()) return
    await page.getByRole('button', { name: 'Plan settings' }).click()
    await page.getByRole('menuitem', { name: /Manage categories/i }).click()
    await page.locator('.pl-catmgr-row:not(.pl-catmgr-addrow)').nth(3).waitFor({ timeout: 10_000 })
    await sleep(400)
  }
  const openFilter = async () => {
    await openManager()
    if (await filterOpen()) return
    await page.getByRole('button', { name: /Filter categories/ }).click()
    await page.locator('.kebab-sheet').waitFor()
    await sleep(400)
  }
  const expectOnlyFilterClosed = async (what) => {
    await sleep(500)
    check((await filterOpen()) === 0, `${what}: the filter sheet closed`)
    check((await parentOpen()) === 1, `${what}: the Manage categories sheet stayed open`)
  }

  await openFilter()
  const state = await page.evaluate(() => {
    const scrim = document.querySelector('.kebab-sheet-scrim')
    const sheet = document.querySelector('.kebab-sheet')
    const row = sheet.querySelector('.pl-catfilter-item')
    const r = sheet.getBoundingClientRect()
    const rr = row.getBoundingClientRect()
    const label = row.querySelector('.pl-catfilter-item-label').getBoundingClientRect()
    const count = row.querySelector('.pl-catfilter-item-count').getBoundingClientRect()
    return {
      bodyPE: document.body.style.pointerEvents,
      scrimPE: getComputedStyle(scrim).pointerEvents,
      sheetPE: getComputedStyle(sheet).pointerEvents,
      scrimZ: Number(getComputedStyle(scrim).zIndex),
      plOvZ: Number(getComputedStyle(document.querySelector('.pl-ov')).zIndex),
      aboveSheet: document.elementFromPoint(195, Math.round(r.top) - 40)?.className,
      onRow:
        document
          .elementFromPoint(Math.round(rr.left + 20), Math.round(rr.top + rr.height / 2))
          ?.closest('.pl-catfilter-item') != null,
      rowDisplay: getComputedStyle(row).display,
      // The label box is `flex: 1`, so measure the count against the TEXT it must not touch and
      // against the row's right edge, never against the stretched label box.
      countClearOfText: count.left - (label.left + label.width / 2),
      countAtRightEdge: rr.right - count.right,
      selectedTick:
        sheet.querySelector('[aria-selected="true"] .pl-catfilter-item-tick')?.textContent === '✓',
      tickCount: Array.from(sheet.querySelectorAll('.pl-catfilter-item-tick')).filter(
        (t) => t.textContent === '✓',
      ).length,
      // The owner's follow-up: the tick must not shift the selected row's count — every row's
      // count ends on the same x.
      countRightEdges: Array.from(sheet.querySelectorAll('.pl-catfilter-item-count'), (c) =>
        Math.round(c.getBoundingClientRect().right),
      ),
    }
  })
  check(
    state.bodyPE === 'none',
    `the host modal has switched body pointer events off (${state.bodyPE}) — the condition under test`,
  )
  check(
    state.scrimPE === 'auto' && state.sheetPE === 'auto',
    `scrim and sheet restore their own pointer events (${state.scrimPE}/${state.sheetPE})`,
  )
  check(
    state.scrimZ >= state.plOvZ,
    `scrim stacks at/above the parent overlay (${state.scrimZ} vs ${state.plOvZ})`,
  )
  check(
    state.aboveSheet === 'kebab-sheet-scrim',
    `the point just above the sheet hits the scrim, not the parent (${state.aboveSheet})`,
  )
  check(state.onRow, 'a point on a filter row hits the row, not the parent table beneath')
  check(state.rowDisplay === 'flex', `rows lay out as flex (${state.rowDisplay})`)
  check(
    state.countClearOfText >= 100 && state.countAtRightEdge <= 40,
    `the count is docked at the row's right edge, clear of the label (${Math.round(state.countClearOfText)}px from mid-label, ${Math.round(state.countAtRightEdge)}px from the edge)`,
  )
  check(
    state.selectedTick && state.tickCount === 1,
    `exactly the selected row carries the tick (${state.tickCount})`,
  )
  check(
    state.countRightEdges.length > 1 && new Set(state.countRightEdges).size === 1,
    `the counts form one aligned column, tick or no tick (right edges: ${state.countRightEdges.join(', ')})`,
  )
  await page.screenshot({ path: join(outputDir, 'filter-open-over-manage-categories.png') })

  const top = await page.locator('.kebab-sheet').evaluate((el) => el.getBoundingClientRect().top)
  await page.touchscreen.tap(195, Math.round(top) - 40)
  await expectOnlyFilterClosed('tap on the scrim')

  await openFilter()
  await page.locator('.kebab-sheet-x').tap()
  await expectOnlyFilterClosed('tap on the X')

  await openFilter()
  await page.getByRole('option', { name: /Expense/ }).tap()
  await expectOnlyFilterClosed('tap on a row')
  check(
    (await page.getByRole('button', { name: 'Filter categories (active)' }).count()) === 1,
    'the pick applied (trigger shows active)',
  )
  const rows = await page.locator('.pl-catmgr-row:not(.pl-catmgr-addrow)').count()
  check(rows > 0, `the category list is still rendered after the pick (${rows} rows)`)

  await openFilter()
  const cdp = await context.newCDPSession(page)
  const hr = await page.locator('.kebab-sheet-head').evaluate((el) => el.getBoundingClientRect())
  const x = Math.round(hr.left + hr.width / 2)
  const y0 = Math.round(hr.top + hr.height / 2)
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y: y0 }] })
  for (let i = 1; i <= 12; i++) {
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x, y: y0 + i * 25 }],
    })
    await sleep(16)
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await expectOnlyFilterClosed('drag the filter sheet down')
  const parentShift = await page.locator('.pl-modal').evaluate((el) => el.style.transform)
  check(
    !parentShift || parentShift === 'none' || parentShift === '',
    `the parent sheet was not dragged along (transform: "${parentShift}")`,
  )

  await openFilter()
  await page.keyboard.press('Escape')
  await expectOnlyFilterClosed('Escape')
  await page.keyboard.press('Escape')
  await sleep(500)
  check((await parentOpen()) === 0, 'a second Escape still closes the Manage categories sheet')

  check(pageErrors.length === 0, `no page errors (${pageErrors.length})`)
  if (pageErrors.length) console.log(pageErrors.slice(0, 5).join('\n'))
  await context.close()
} finally {
  await browser.close()
  app.stop()
}

console.log(`\nEvidence: ${outputDir}`)
if (failures.length > 0) {
  console.error(`\ncatfilter-in-modal: FAIL (${failures.length})`)
  process.exit(1)
}
console.log('\ncatfilter-in-modal: PASS')
