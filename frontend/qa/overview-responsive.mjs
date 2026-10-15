import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const evidenceDir = join(repoRoot, 'docs', 'qa', 'evidence', 'task-115', 'after')
const baseUrl = process.argv[2] || 'http://127.0.0.1:8115'
const kpiLabels = ['Today', 'Unrealized P&L', 'Cash on hand', 'Liabilities']
const results = {
  baseUrl,
  netWorth: {},
  shapeAudit: {},
  interactions: {},
}

mkdirSync(evidenceDir, { recursive: true })

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

async function settle(page) {
  await page.waitForTimeout(800)
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  )
}

async function visibleWithin(locator, container) {
  const [box, containerBox] = await Promise.all([locator.boundingBox(), container.boundingBox()])
  return (
    !!box &&
    !!containerBox &&
    box.width > 0 &&
    box.height > 0 &&
    box.x >= containerBox.x - 1 &&
    box.x + box.width <= containerBox.x + containerBox.width + 1 &&
    box.y >= containerBox.y - 1 &&
    box.y + box.height <= containerBox.y + containerBox.height + 1
  )
}

async function measureNetWorth(page, name, targetWidth) {
  const grid = page.locator('.ov-grid')
  await grid.evaluate((node, width) => {
    node.style.width = `${width + 16}px`
  }, targetWidth)
  await settle(page)

  const card = page.locator('.ov-griditem').filter({ has: page.locator('.ov-kpis') })
  const panel = card.locator('.panel')
  const kpis = card.locator('.ov-kpi')
  await card.waitFor({ state: 'visible' })
  const metric = await card.evaluate((node) => {
    const panel = node.querySelector('.panel')
    const grid = node.querySelector('.ov-kpis')
    const next = [...node.parentElement.children]
      .filter((candidate) => candidate !== node && candidate.classList.contains('ov-griditem'))
      .map((candidate) => candidate.getBoundingClientRect())
      .filter((rect) => rect.top >= node.getBoundingClientRect().bottom - 1)
      .sort((a, b) => a.top - b.top)[0]
    const cardRect = node.getBoundingClientRect()
    const panelRect = panel.getBoundingClientRect()
    return {
      cardWidth: Math.round(cardRect.width),
      cardHeight: Math.round(cardRect.height),
      panelClientHeight: panel.clientHeight,
      panelScrollHeight: panel.scrollHeight,
      columns: getComputedStyle(grid).gridTemplateColumns.split(' ').length,
      nextGap: next ? Math.round(next.top - cardRect.bottom) : null,
      pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }
  })
  const labels = await kpis.locator('.ov-kpi-l').allTextContents()
  const values = await kpis.locator('.ov-kpi-v').allTextContents()
  const allKpisVisible = await Promise.all(
    Array.from({ length: await kpis.count() }, (_, index) => visibleWithin(kpis.nth(index), panel)),
  )
  metric.panelBox = await panel.boundingBox()
  metric.kpiBoxes = await Promise.all(
    Array.from({ length: await kpis.count() }, (_, index) => kpis.nth(index).boundingBox()),
  )
  const samples = []
  for (let index = 0; index < 6; index += 1) {
    samples.push(Math.round((await card.boundingBox()).height))
    await page.waitForTimeout(100)
  }
  metric.stableHeights = [...new Set(samples)]
  metric.labels = labels
  metric.values = values
  metric.allKpisVisible = allKpisVisible.every(Boolean)
  console.log(`net-worth ${name}`, metric)

  assert(Math.abs(metric.cardWidth - targetWidth) <= 2, `${name}: card width did not settle`)
  assert(labels.length === 4, `${name}: expected four KPI labels`)
  assert(
    kpiLabels.every((label) => labels.includes(label)),
    `${name}: a required KPI label is missing`,
  )
  assert(
    values.every((value) => value.trim()),
    `${name}: a KPI amount is empty`,
  )
  assert(metric.allKpisVisible, `${name}: a KPI is clipped`)
  assert(
    metric.panelScrollHeight <= metric.panelClientHeight + 1,
    `${name}: panel content overflows vertically`,
  )
  assert(metric.nextGap == null || metric.nextGap >= 15, `${name}: next widget overlaps`)
  assert(metric.stableHeights.length === 1, `${name}: height oscillated`)
  assert(
    metric.columns === (targetWidth > 460 ? 4 : targetWidth > 280 ? 2 : 1),
    `${name}: unexpected KPI column count`,
  )

  await card.screenshot({ path: join(evidenceDir, `net-worth-${name}.png`) })
  results.netWorth[name] = metric
}

async function auditView(page, name) {
  await page.getByRole('tab', { name, exact: true }).click()
  await page.locator('.ov-griditem').first().waitFor({ state: 'visible' })
  await page.locator('.ov-grid').evaluate((node) => {
    node.style.width = '800px'
  })
  await settle(page)
  const audit = await page.locator('.ov-grid').evaluate((grid) => {
    const cells = [...grid.querySelectorAll('.ov-griditem')].map((cell) => {
      const rect = cell.getBoundingClientRect()
      const title = cell.querySelector('.ui-panel__title')?.textContent?.trim() || 'Section'
      const panel = cell.querySelector('.ov-griditem__body > .panel, .ov-griditem__body > section')
      return {
        title,
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        unsafeOverflow:
          !!panel &&
          panel.scrollHeight > panel.clientHeight + 1 &&
          !['auto', 'scroll'].includes(getComputedStyle(panel).overflowY),
      }
    })
    const overlaps = []
    for (let left = 0; left < cells.length; left += 1) {
      for (let right = left + 1; right < cells.length; right += 1) {
        const a = cells[left]
        const b = cells[right]
        if (
          a.left < b.right - 1 &&
          b.left < a.right - 1 &&
          a.top < b.bottom - 1 &&
          b.top < a.bottom - 1
        ) {
          overlaps.push(`${a.title} / ${b.title}`)
        }
      }
    }
    return {
      cardCount: cells.length,
      overlaps,
      unsafeOverflow: cells.filter((cell) => cell.unsafeOverflow).map((cell) => cell.title),
      chartCount: grid.querySelectorAll('canvas, .ov-chartwrap').length,
      tableCount: grid.querySelectorAll('.ov-iet, table').length,
      listCount: grid.querySelectorAll('.ov-up-row, .ov-mv-row, .ov-breakdown-row').length,
      proseCount: grid.querySelectorAll('.ov-griditem--autoh').length,
    }
  })
  assert(audit.overlaps.length === 0, `${name}: widgets overlap`)
  assert(audit.unsafeOverflow.length === 0, `${name}: unsafe clipped panels`)
  results.shapeAudit[name] = audit
}

const browser = await chromium.launch()
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
})
const consoleMessages = []
const page = await context.newPage()
page.on('console', (message) => {
  if (
    message.type() === 'error' ||
    /ResizeObserver loop|undelivered notifications/i.test(message.text())
  ) {
    consoleMessages.push(`${message.type()}: ${message.text()}`)
  }
})
await context.addInitScript(() => {
  localStorage.setItem('wealthTab', 'overview')
  localStorage.setItem('chat-open', 'false')
  localStorage.setItem('wealthHide', '0')
})

try {
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  await page.locator('.ov-kpis').waitFor({ state: 'visible', timeout: 20_000 })
  await settle(page)

  await measureNetWorth(page, 'wide', 1100)
  await measureNetWorth(page, 'medium', 440)
  await measureNetWorth(page, 'narrow', 250)

  await page.locator('.ov-grid').evaluate((node) => {
    node.style.width = ''
  })
  await page.setViewportSize({ width: 390, height: 900 })
  await settle(page)
  const mobileCard = page.locator('.ov-griditem').filter({ has: page.locator('.ov-kpis') })
  const mobilePanel = mobileCard.locator('.panel')
  const mobileMetric = await mobileCard.evaluate((node) => {
    const panel = node.querySelector('.panel')
    return {
      cardWidth: Math.round(node.getBoundingClientRect().width),
      panelClientHeight: panel.clientHeight,
      panelScrollHeight: panel.scrollHeight,
      columns: getComputedStyle(node.querySelector('.ov-kpis')).gridTemplateColumns.split(' ')
        .length,
      pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }
  })
  mobileMetric.allKpisVisible = (
    await Promise.all(
      Array.from({ length: 4 }, (_, index) =>
        visibleWithin(mobileCard.locator('.ov-kpi').nth(index), mobilePanel),
      ),
    )
  ).every(Boolean)
  assert(mobileMetric.allKpisVisible, 'mobile: a KPI is clipped')
  assert(mobileMetric.pageOverflow <= 0, 'mobile: page has horizontal overflow')
  await mobileCard.screenshot({ path: join(evidenceDir, 'net-worth-mobile.png') })
  results.netWorth.mobile = mobileMetric

  await page.setViewportSize({ width: 1440, height: 1000 })
  await auditView(page, 'Overview')
  await auditView(page, 'Reports')

  await page.getByRole('tab', { name: 'Overview', exact: true }).click()
  await page.locator('.ov-kpis').waitFor({ state: 'visible' })
  await page.locator('.ov-grid').evaluate((node) => {
    node.style.width = ''
  })
  await settle(page)
  await page.getByRole('button', { name: 'Customize', exact: true }).click()
  const editableHero = page.locator('.ov-griditem').filter({ has: page.locator('.ov-kpis') })
  const beforeResize = await editableHero.boundingBox()
  const resizeHandle = editableHero.locator('.ov-resize-handle--e-r')
  const handleBox = await resizeHandle.boundingBox()
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2)
  await page.mouse.down()
  await page.mouse.move(handleBox.x - 260, handleBox.y + handleBox.height / 2, { steps: 8 })
  await page.mouse.up()
  await settle(page)
  const afterResize = await editableHero.boundingBox()
  assert(afterResize.width < beforeResize.width - 100, 'customize: width resize did not apply')
  results.interactions.resize = {
    beforeWidth: Math.round(beforeResize.width),
    afterWidth: Math.round(afterResize.width),
  }
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.locator('.ov-kpis').waitFor({ state: 'visible' })
  await editableHero.hover()
  await editableHero.getByRole('button', { name: 'Full screen', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: /full screen/ })
  await dialog.waitFor({ state: 'visible' })
  results.interactions.fullscreen = await dialog.isVisible()
  await dialog.getByRole('button', { name: 'Close', exact: true }).click()
  await dialog.waitFor({ state: 'hidden' })

  results.consoleMessages = consoleMessages
  assert(consoleMessages.length === 0, `console errors: ${consoleMessages.join('; ')}`)
  writeFileSync(
    join(evidenceDir, 'responsive-metrics.json'),
    `${JSON.stringify(results, null, 2)}\n`,
  )
  console.log(JSON.stringify(results, null, 2))
} finally {
  await context.close()
  await browser.close()
}
