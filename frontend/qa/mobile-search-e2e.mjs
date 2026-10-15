import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { bootDisposableInstance } from './boot.mjs'

const outputDir = process.env.ONW_MOBILE_SEARCH_EVIDENCE || '/tmp/onw-task-219-mobile-search'
const port = Number(process.env.ONW_MOBILE_SEARCH_PORT || 8793)
mkdirSync(outputDir, { recursive: true })

const app = await bootDisposableInstance({ port })
const browser = await chromium.launch({ headless: true })

try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
  await page.goto(app.baseUrl, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: 'More' }).waitFor()

  await page.getByRole('button', { name: 'More' }).click()
  await page.getByRole('button', { name: 'Search', exact: true }).click()

  const input = page.getByRole('textbox', { name: 'Search or ask Lucius' })
  await input.waitFor()
  if (!(await input.evaluate((element) => element === document.activeElement))) {
    throw new Error('mobile search did not focus its field on open')
  }

  const initialInputBox = await input.boundingBox()
  const initialScrollY = await page.evaluate(() => window.scrollY)
  if (!initialInputBox || initialInputBox.y > 80) {
    throw new Error(`mobile search field is not immediately visible: ${JSON.stringify(initialInputBox)}`)
  }
  if (initialScrollY !== 0) throw new Error(`document scrolled while opening search: ${initialScrollY}`)
  await page.screenshot({ path: join(outputDir, 'mobile-search-open.png'), fullPage: false })

  await input.fill('investments')
  const investment = page.getByRole('option').filter({ hasText: 'Investments' }).first()
  await investment.waitFor()

  // Shrinking the viewport is how Playwright simulates the keyboard: `interactive-widget=
  // resizes-content` makes a real keyboard shrink the layout viewport the same way, which is
  // why the palette needs no measurement to sit above it.
  //
  // Waits on the RENDERED geometry, not on a custom property. This used to poll for
  // `--mobile-visual-viewport-height === '510px'`, set by a keyboard-measuring JS hook that
  // has since been removed (see components/mobile/mobileKeyboard.contract.test.ts) — so the
  // poll would now hang forever on a palette that is in fact laid out correctly.
  await page.setViewportSize({ width: 390, height: 510 })
  await page.waitForFunction(() => {
    const overlay = document.querySelector('.modal-overlay:has(.cmdk)')
    return !!overlay && Math.abs(overlay.getBoundingClientRect().height - 510) <= 1
  })

  const reducedInputBox = await input.boundingBox()
  const dialogBox = await page.getByRole('dialog').boundingBox()
  const resultBox = await investment.boundingBox()
  if (!reducedInputBox || reducedInputBox.y > 80) {
    throw new Error(`field left the reduced visual viewport: ${JSON.stringify(reducedInputBox)}`)
  }
  if (!dialogBox || Math.abs(dialogBox.height - 510) > 1) {
    throw new Error(`palette did not follow the reduced visual viewport: ${JSON.stringify(dialogBox)}`)
  }
  if (!resultBox || resultBox.y + resultBox.height > 510) {
    throw new Error(`no complete matching result remains above the keyboard edge: ${JSON.stringify(resultBox)}`)
  }

  const headerTopBeforeScroll = reducedInputBox.y
  await page.locator('.cmdk-results').evaluate((element) => {
    element.scrollTop = Math.min(160, element.scrollHeight - element.clientHeight)
  })
  const headerTopAfterScroll = (await input.boundingBox())?.y
  const documentScrollAfterResults = await page.evaluate(() => window.scrollY)
  if (headerTopAfterScroll !== headerTopBeforeScroll) {
    throw new Error(`results scrolling moved the search header: ${headerTopBeforeScroll} -> ${headerTopAfterScroll}`)
  }
  if (documentScrollAfterResults !== initialScrollY) {
    throw new Error(`results scrolling moved the document: ${initialScrollY} -> ${documentScrollAfterResults}`)
  }
  await page.screenshot({ path: join(outputDir, 'mobile-search-keyboard-height.png'), fullPage: false })

  await investment.click()
  await page.getByRole('dialog').waitFor({ state: 'detached' })

  await page.setViewportSize({ width: 1280, height: 800 })
  await page.keyboard.press('Meta+k')
  const desktopDialog = page.getByRole('dialog')
  await desktopDialog.waitFor()
  const desktopBox = await desktopDialog.boundingBox()
  if (!desktopBox || desktopBox.width >= 1000 || desktopBox.height >= 760 || desktopBox.y <= 0) {
    throw new Error(`desktop Cmd+K no longer renders as a floating dialog: ${JSON.stringify(desktopBox)}`)
  }
  await page.locator('.cmdk-footer').waitFor()
  await page.screenshot({ path: join(outputDir, 'desktop-cmdk-preserved.png'), fullPage: false })
  await page.keyboard.press('Escape')
  await desktopDialog.waitFor({ state: 'detached' })

  console.log(`mobile-search-e2e: PASS (${outputDir})`)
} finally {
  await browser.close()
  app.stop()
}
