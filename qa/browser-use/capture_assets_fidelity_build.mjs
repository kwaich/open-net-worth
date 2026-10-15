import { chromium } from '../../frontend/node_modules/playwright/index.mjs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { mkdir } from 'node:fs/promises'

const here = path.dirname(fileURLToPath(import.meta.url))
const dir = path.join(here, 'artifacts', 'assets-fidelity', 'build')
await mkdir(dir, { recursive: true })

const BASE = process.env.ASSETS_BUILD_URL || 'http://127.0.0.1:8099'
const CHAT = process.argv.includes('--chat-open') ? '1' : '0'
const suffix = CHAT === '1' ? '-chatopen' : ''

const widths = [
  { width: 1440, height: 900, label: '1440' },
  { width: 1024, height: 800, label: '1024' },
  { width: 390, height: 844, label: '390' },
]

const browser = await chromium.launch({ headless: true })

async function newCtxPage(w) {
  const ctx = await browser.newContext({ viewport: { width: w.width, height: w.height }, colorScheme: 'dark' })
  await ctx.addInitScript(([chatOpen]) => {
    localStorage.setItem('chat-open', chatOpen)
    localStorage.setItem('onw-theme', 'dark')
  }, [CHAT])
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  page._errors = errors
  return { ctx, page }
}

for (const w of widths) {
  const { ctx, page } = await newCtxPage(w)
  await page.goto(`${BASE}/assets`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  await page.screenshot({ path: path.join(dir, `register-${w.label}${suffix}.png`), fullPage: true })

  const catBtn = page.locator('.as-bar .as-chip').nth(1)
  if (await catBtn.count()) {
    await catBtn.click().catch(() => {})
    await page.waitForTimeout(300)
    await page.screenshot({ path: path.join(dir, `filter-${w.label}${suffix}.png`), fullPage: true })
    const allBtn = page.locator('.as-bar .as-chip').first()
    await allBtn.click().catch(() => {})
    await page.waitForTimeout(200)
  }

  const firstCard = page.locator('.as-card').filter({ hasText: 'Marina Rise' }).first()
  if (await firstCard.count()) {
    await firstCard.click().catch(() => {})
    await page.waitForTimeout(600)
    await page.screenshot({ path: path.join(dir, `detail-${w.label}${suffix}.png`), fullPage: true })
    const back = page.locator('.as-crumb button').first()
    if (await back.count()) { await back.click().catch(() => {}); await page.waitForTimeout(300) }
  }

  if (w._errors && w._errors.length) console.log(`[build ${w.label}] console errors:`, w._errors.slice(0, 5))
  if (page._errors.length) console.log(`[build ${w.label}] console errors:`, page._errors.slice(0, 5))
  await ctx.close()
}

// Add modal, Edit, Update value, Dispose — desktop only, no chat-state suffix duplication needed but keep consistent
{
  const { ctx, page } = await newCtxPage({ width: 1440, height: 900 })
  await page.goto(`${BASE}/assets`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(700)

  const addBtn = page.locator('button:has-text("Add asset")').first()
  if (await addBtn.count()) {
    await addBtn.click()
    await page.waitForTimeout(400)
    await page.screenshot({ path: path.join(dir, `add-modal-door${suffix}.png`) })
    const enterManually = page.locator('.as-door').first()
    if (await enterManually.count()) {
      await enterManually.click().catch(() => {})
      await page.waitForTimeout(300)
      await page.screenshot({ path: path.join(dir, `add-modal-category${suffix}.png`) })
      const catOption = page.locator('.as-pick button:has-text("Property")').first()
      if (await catOption.count()) {
        await catOption.click().catch(() => {})
        await page.waitForTimeout(300)
        await page.screenshot({ path: path.join(dir, `add-modal-form${suffix}.png`) })
      }
    }
    await page.keyboard.press('Escape').catch(() => {})
    await page.waitForTimeout(200)
  }

  const firstCard = page.locator('.as-card').filter({ hasText: 'Marina Rise' }).first()
  if (await firstCard.count()) {
    await firstCard.click().catch(() => {})
    await page.waitForTimeout(500)

    const updateBtn = page.locator('button:has-text("Update value")').first()
    if (await updateBtn.count()) {
      await updateBtn.click().catch(() => {})
      await page.waitForTimeout(300)
      await page.screenshot({ path: path.join(dir, `update-value-modal${suffix}.png`) })
      await page.keyboard.press('Escape').catch(() => {})
      await page.waitForTimeout(200)
    }

    const moreBtn = page.locator('.as-more').first()
    if (await moreBtn.count()) {
      await moreBtn.click().catch(() => {})
      await page.waitForTimeout(200)
      const editBtn = page.locator('button:has-text("Edit details")').first()
      if (await editBtn.count()) {
        await editBtn.click().catch(() => {})
        await page.waitForTimeout(300)
        await page.screenshot({ path: path.join(dir, `edit-modal${suffix}.png`) })
        await page.keyboard.press('Escape').catch(() => {})
        await page.waitForTimeout(200)
      }
    }

    const moreBtn2 = page.locator('.as-more').first()
    if (await moreBtn2.count()) {
      await moreBtn2.click().catch(() => {})
      await page.waitForTimeout(200)
      const disposeBtn = page.locator('button:has-text("Sell or dispose")').first()
      if (await disposeBtn.count()) {
        await disposeBtn.click().catch(() => {})
        await page.waitForTimeout(300)
        await page.screenshot({ path: path.join(dir, `dispose-modal${suffix}.png`) })
        await page.keyboard.press('Escape').catch(() => {})
      }
    }
  }
  await ctx.close()
}

await browser.close()
console.log('done')
