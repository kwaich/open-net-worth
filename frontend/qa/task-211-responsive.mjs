import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const evidenceDir = join(repoRoot, 'docs', 'qa', 'evidence', 'task-211', 'after')
const baseUrl = process.argv[2] || 'http://127.0.0.1:8093'
const phoneWidths = [320, 390, 640]
const results = { baseUrl, investments: {}, plan: {}, desktop: {} }

mkdirSync(evidenceDir, { recursive: true })

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

async function settle(page) {
  await page.waitForTimeout(500)
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  )
}

const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
await context.addInitScript(() => {
  localStorage.setItem('chat-open', 'false')
  localStorage.setItem('wealthHide', '0')
  localStorage.setItem('inv-holdings-density', 'cards')
  localStorage.setItem('inv-holdings-groupby', 'none')
})
const page = await context.newPage()

await page.route('**/api/portfolio', async (route) => {
  const response = await route.fetch()
  const rows = await response.json()
  const populated = rows.map((row, index) => ({
    ...row,
    ticker: row.ticker || row.symbol || `HOLD${index + 1}`,
    ...(index === 0
      ? {
          label: 'Long identity remains readable',
          quantity: 0.00005,
          price: 1234567.89,
          cost_basis: 1098901.23,
          quote_currency: 'KWD',
          prev_price: 1098901.23,
          day_change_pct: 12.34,
        }
      : {}),
  }))
  await route.fulfill({ response, json: populated })
})

try {
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 })

  for (const width of phoneWidths) {
    await page.setViewportSize({ width, height: 844 })
    await page.evaluate(() => localStorage.setItem('wealthTab', 'investments'))
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await page.locator('.mk-toolbar').waitFor({ state: 'visible' })
    await page.locator('.mk-hold').first().waitFor({ state: 'visible' })
    await settle(page)

    const metric = await page.evaluate(() => {
      const toolbar = document.querySelector('.mk-toolbar')
      const controls = [...toolbar.children]
      const controlRects = controls.map((node) => node.getBoundingClientRect())
      const centers = controlRects.map((rect) => rect.top + rect.height / 2)
      const quote = document.querySelector('.mk-market-move[aria-label*="KWD"]')
      const card = quote.closest('.mk-hold')
      const identity = card.querySelector('.mk-id')
      const metrics = card.querySelector('.mk-card-metrics')
      const cardRect = card.getBoundingClientRect()
      const identityRect = identity.getBoundingClientRect()
      const metricsRect = metrics.getBoundingClientRect()
      const quoteRect = quote.getBoundingClientRect()
      const selectedAccount = toolbar.querySelector('.mk-toolbar > .mk-toolsel option:checked')
      return {
        pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        toolbarOverflow: toolbar.scrollWidth - toolbar.clientWidth,
        toolbarCenterSpread: Math.max(...centers) - Math.min(...centers),
        toolbarControlCount: controls.length,
        selectedAccount: selectedAccount?.textContent?.trim(),
        accountWidth: Math.round(
          toolbar.querySelector(':scope > .mk-toolsel').getBoundingClientRect().width,
        ),
        groupWidth: Math.round(toolbar.querySelector('.mk-group-wrap').getBoundingClientRect().width),
        searchPlaceholderColor: getComputedStyle(toolbar.querySelector('.mk-search'), '::placeholder').color,
        cardHeight: Math.round(cardRect.height),
        identityWidth: Math.round(identityRect.width),
        quoteWidth: Math.round(quoteRect.width),
        identityQuoteGap: Math.round(quoteRect.left - identityRect.right),
        metricsQuoteGap: Math.round(quoteRect.left - metricsRect.right),
        quoteCenterDelta: Math.round(
          Math.abs(quoteRect.top + quoteRect.height / 2 - (cardRect.top + cardRect.height / 2)),
        ),
        quoteText: quote.textContent.trim(),
        quoteAria: quote.getAttribute('aria-label'),
        latestPriceVisible: card.textContent.includes('Latest price'),
      }
    })
    console.log(`investments ${width}px`, metric)

    assert(metric.pageOverflow <= 1, `Investments page overflows at ${width}px`)
    assert(metric.toolbarOverflow <= 1, `Investments toolbar overflows at ${width}px`)
    assert(metric.toolbarCenterSpread <= 1, `Investments controls wrap at ${width}px`)
    assert(metric.toolbarControlCount === 4, `Expected four toolbar control groups at ${width}px`)
    assert(metric.selectedAccount === 'All accounts', `All accounts is not visible at ${width}px`)
    assert(metric.accountWidth === 128, `All accounts control is compressed at ${width}px`)
    assert(metric.groupWidth === 38, `Grouping control is not icon-sized at ${width}px`)
    assert(metric.identityWidth >= 132, `Long quote squeezes holding identity at ${width}px`)
    assert(metric.identityQuoteGap >= 6, `Long quote overlaps holding identity at ${width}px`)
    assert(metric.metricsQuoteGap >= 6, `Long quote overlaps holding metrics at ${width}px`)
    assert(metric.quoteCenterDelta <= 2, `Quote rail is not vertically centered at ${width}px`)
    assert(metric.quoteText.includes('KWD'), `Long quote currency is missing at ${width}px`)
    assert(metric.quoteText.includes('%'), `Today percentage is missing at ${width}px`)
    assert(!metric.latestPriceVisible, `Latest price label is visible at ${width}px`)
    results.investments[width] = metric

    if (width === 320 || width === 390) {
      await page.locator('.mk-toolbar').scrollIntoViewIfNeeded()
      await settle(page)
      await page.screenshot({
        path: join(evidenceDir, `investments-${width}.png`),
        fullPage: false,
      })
    }

    await page.evaluate(() => localStorage.setItem('wealthTab', 'transactions'))
    await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await page.locator('.pl-page > .ui-page-header').waitFor({ state: 'visible' })
    await page.locator('.pl-summary--pinned').waitFor({ state: 'visible' })
    await settle(page)

    const planMetric = await page.evaluate(() => {
      const header = document.querySelector('.pl-page > .ui-page-header')
      const title = header.querySelector('.ui-page-header__title')
      const actions = header.querySelector('.ui-page-header__actions')
      const subtitle = header.querySelector('.ui-page-header__subtitle')
      const summary = document.querySelector('.pl-summary--pinned')
      const titleRect = title.getBoundingClientRect()
      const actionRect = actions.getBoundingClientRect()
      return {
        pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        headerOverflow: header.scrollWidth - header.clientWidth,
        titleActionCenterDelta: Math.round(
          Math.abs(
            titleRect.top + titleRect.height / 2 - (actionRect.top + actionRect.height / 2),
          ),
        ),
        subtitleDisplay: getComputedStyle(subtitle).display,
        fireLabel: actions.textContent.trim(),
        summaryHeight: Math.round(summary.getBoundingClientRect().height),
        summaryOverflow: summary.scrollWidth - summary.clientWidth,
      }
    })
    console.log(`plan ${width}px`, planMetric)

    assert(planMetric.pageOverflow <= 1, `Plan page overflows at ${width}px`)
    assert(planMetric.headerOverflow <= 1, `Plan header overflows at ${width}px`)
    assert(planMetric.titleActionCenterDelta <= 2, `FIRE settings wraps below Plan at ${width}px`)
    assert(planMetric.subtitleDisplay === 'none', `Plan subtitle is visible at ${width}px`)
    assert(/FIRE settings/i.test(planMetric.fireLabel), `FIRE settings is missing at ${width}px`)
    assert(planMetric.summaryHeight <= 170, `Ready to assign is too tall at ${width}px`)
    assert(planMetric.summaryOverflow <= 1, `Ready to assign overflows at ${width}px`)
    results.plan[width] = planMetric

    if (width === 320 || width === 390) {
      await page.screenshot({
        path: join(evidenceDir, `plan-${width}.png`),
        fullPage: true,
      })
    }
  }

  await page.setViewportSize({ width: 1024, height: 900 })
  await page.evaluate(() => localStorage.setItem('wealthTab', 'transactions'))
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  await settle(page)
  results.desktop.planSubtitleDisplay = await page
    .locator('.pl-page .ui-page-header__subtitle')
    .evaluate((node) => getComputedStyle(node).display)
  assert(
    results.desktop.planSubtitleDisplay !== 'none',
    'Desktop Plan subtitle was unintentionally hidden',
  )

  writeFileSync(
    join(evidenceDir, 'responsive-metrics.json'),
    `${JSON.stringify(results, null, 2)}\n`,
  )
  console.log(JSON.stringify(results, null, 2))
} finally {
  await browser.close()
}
