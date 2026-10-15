// Targeted, one-off smoke check for the new Lucius home tab (2026-08-12 feature).
// Self-boots a disposable, isolated instance (same infra as scripts/visual-qa.mjs --boot)
// and screenshots specifically /lucius at desktop + mobile widths, plus a sidebar-collapsed
// shot and a second-tab-navigation shot (confirms the floating pill reappears elsewhere and
// disappears again back on /lucius). Not a permanent QA script — ad hoc verification for this
// change; scripts/visual-qa.mjs's own TABS list is a separate, pre-existing gap (out of scope
// here) that should add 'lucius' in a follow-up.

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { bootDisposableInstance } from './boot.mjs'

const outDir = '/tmp/lucius-home-smoke'
mkdirSync(outDir, { recursive: true })

const TIMEOUT_MS = 90_000
const timeout = setTimeout(() => {
  console.error('TIMEOUT — hard cap hit')
  process.exit(99)
}, TIMEOUT_MS)
timeout.unref?.()

async function main() {
  const instance = await bootDisposableInstance({ port: 8766 })
  console.log(`[smoke] booted at ${instance.baseUrl}`)
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
    const consoleErrors = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })
    page.on('pageerror', (err) => consoleErrors.push(String(err)))

    // Clear localStorage so the index redirect exercises the NEW default-landing-tab logic.
    await page.goto(instance.baseUrl)
    await page.evaluate(() => localStorage.clear())

    // 1. Fresh desktop visit — should land on /lucius by default.
    await page.goto(instance.baseUrl + '/')
    await page.waitForTimeout(1500)
    const url1 = page.url()
    console.log(`[smoke] fresh desktop visit landed on: ${url1}`)
    await page.screenshot({ path: `${outDir}/01-fresh-desktop-landing.png`, fullPage: false })

    // 2. Sidebar + main pane visible, greeting present.
    const bodyText = await page.evaluate(() => document.body.innerText)
    const hasGreeting = /Good (morning|afternoon|evening)/.test(bodyText)
    const hasNewChat = bodyText.includes('New chat')
    console.log(`[smoke] greeting present: ${hasGreeting}, "New chat" present: ${hasNewChat}`)

    // 3. Collapse the sidebar.
    const collapseBtn = page.getByLabel('Hide chat history')
    if (await collapseBtn.count()) {
      await collapseBtn.click()
      await page.waitForTimeout(300)
      await page.screenshot({ path: `${outDir}/02-sidebar-collapsed.png` })
      const reopenBtn = page.getByLabel('Show chat history')
      console.log(`[smoke] reopen button present after collapse: ${(await reopenBtn.count()) > 0}`)
      if (await reopenBtn.count()) {
        await reopenBtn.click()
        await page.waitForTimeout(300)
        await page.screenshot({ path: `${outDir}/03-sidebar-reopened.png` })
      }
    } else {
      console.log('[smoke] WARNING: collapse button not found')
    }

    // 4. Navigate to another tab — floating pill should reappear.
    await page.goto(instance.baseUrl + '/investments')
    await page.waitForTimeout(1000)
    await page.screenshot({ path: `${outDir}/04-investments-tab.png` })
    const pillVisibleElsewhere = (await page.locator('.lx-dock-pill').count()) > 0
    const topbarBtnElsewhere = (await page.locator('#chat-toggle-btn').count()) > 0
    console.log(`[smoke] LuciusDock pill (.lx-dock-pill) visible on Investments tab: ${pillVisibleElsewhere}`)
    console.log(`[smoke] topbar "Lucius ✦" quick-open button visible on Investments tab: ${topbarBtnElsewhere}`)

    // 5. Navigate back to /lucius — pill should be gone again.
    await page.goto(instance.baseUrl + '/lucius')
    await page.waitForTimeout(1000)
    await page.screenshot({ path: `${outDir}/05-back-to-lucius.png` })
    const pillVisibleOnLucius = (await page.locator('.lx-dock-pill').count()) > 0
    const topbarBtnOnLucius = (await page.locator('#chat-toggle-btn').count()) > 0
    console.log(`[smoke] LuciusDock pill visible back on Lucius tab: ${pillVisibleOnLucius} (expect false)`)
    console.log(`[smoke] topbar "Lucius ✦" quick-open button visible on Lucius tab: ${topbarBtnOnLucius} (expect false)`)

    // 5b. Open "View all conversations" — should sit in a padded, non-edge-to-edge column.
    await page.getByText('View all conversations').click()
    await page.waitForTimeout(500)
    await page.screenshot({ path: `${outDir}/05b-view-all-conversations.png` })

    // 6. Nav rail — Lucius should be first icon.
    const firstNavLabel = await page.locator('.sidebar-nav .sidebar-item').first().getAttribute('title')
    console.log(`[smoke] first nav rail item title: "${firstNavLabel}" (expect "Lucius")`)

    // 7. Mobile viewport smoke — bottom tab bar Lucius slot still full-screen, no sidebar.
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(instance.baseUrl + '/lucius')
    await page.waitForTimeout(1000)
    await page.screenshot({ path: `${outDir}/06-mobile-direct-lucius-url.png` })
    const mobileHasSidebar = (await page.locator('.lucius-home-side').count()) > 0
    console.log(`[smoke] mobile /lucius shows desktop sidebar: ${mobileHasSidebar} (expect false)`)

    console.log(`\n[smoke] console errors captured: ${consoleErrors.length}`)
    consoleErrors.slice(0, 10).forEach((e) => console.log('  -', e))

    console.log(`\n[smoke] screenshots written to ${outDir}`)
  } finally {
    await browser.close()
    instance.stop()
  }
}

main()
  .then(() => {
    clearTimeout(timeout)
    process.exit(0)
  })
  .catch((e) => {
    clearTimeout(timeout)
    console.error('[smoke] FAILED:', e)
    process.exit(1)
  })
