import { chromium } from '../../frontend/node_modules/playwright/index.mjs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { mkdir } from 'node:fs/promises'

const here = path.dirname(fileURLToPath(import.meta.url))
const outRoot = path.join(here, 'artifacts', 'assets-fidelity')
await mkdir(path.join(outRoot, 'prototype'), { recursive: true })
await mkdir(path.join(outRoot, 'build'), { recursive: true })

const widths = [
  { width: 1440, height: 900, label: '1440' },
  { width: 1024, height: 800, label: '1024' },
  { width: 390, height: 844, label: '390' },
]

const browser = await chromium.launch({ headless: true })

async function shootPrototype() {
  const dir = path.join(outRoot, 'prototype')
  for (const w of widths) {
    const page = await browser.newPage({ viewport: { width: w.width, height: w.height } })
    const errors = []
    page.on('pageerror', (e) => errors.push(String(e)))
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
    await page.goto('http://127.0.0.1:8097/Assets%20Tab.html', { waitUntil: 'networkidle' })
    await page.waitForTimeout(600)
    await page.screenshot({ path: path.join(dir, `register-${w.label}.png`), fullPage: true })

    // category filter active — click the "Property" pill/chip if present
    const catBtn = page.locator('.as-bar .as-chip').nth(1)
    if (await catBtn.count()) {
      await catBtn.click().catch(() => {})
      await page.waitForTimeout(300)
      await page.screenshot({ path: path.join(dir, `filter-${w.label}.png`), fullPage: true })
      // reset filter
      const allBtn = page.locator('.as-bar .as-chip').first()
      await allBtn.click().catch(() => {})
      await page.waitForTimeout(200)
    }

    // detail view — click the first asset card
    const firstCard = page.locator('.as-card').filter({ hasText: 'Marina Rise' }).first()
    if (await firstCard.count()) {
      await firstCard.click().catch(() => {})
      await page.waitForTimeout(500)
      await page.screenshot({ path: path.join(dir, `detail-${w.label}.png`), fullPage: true })
      // go back to register (best-effort)
      const back = page.locator('.as-crumb button').first()
      if (await back.count()) { await back.click().catch(() => {}); await page.waitForTimeout(300) }
    }

    if (errors.length) console.log(`[prototype ${w.label}] console errors:`, errors.slice(0, 5))
    await page.close()
  }

  // Add modal steps, Edit, Update value, Dispose — desktop only for chrome fidelity
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  await page.goto('http://127.0.0.1:8097/Assets%20Tab.html', { waitUntil: 'networkidle' })
  await page.waitForTimeout(500)
  const addBtn = page.locator('button:has-text("Add asset")').first()
  if (await addBtn.count()) {
    await addBtn.click()
    await page.waitForTimeout(400)
    await page.screenshot({ path: path.join(dir, 'add-modal-door.png') })
    const enterManually = page.locator('.as-door').first()
    if (await enterManually.count()) {
      await enterManually.click().catch(() => {})
      await page.waitForTimeout(300)
      await page.screenshot({ path: path.join(dir, 'add-modal-category.png') })
      const catOption = page.locator('.as-pick button:has-text("Property")').first()
      if (await catOption.count()) {
        await catOption.click().catch(() => {})
        await page.waitForTimeout(300)
        await page.screenshot({ path: path.join(dir, 'add-modal-form.png') })
      }
    }
    // close modal
    const closeBtn = page.locator('[aria-label="Close"], .modal-close, button:has-text("Cancel")').first()
    if (await closeBtn.count()) await closeBtn.click().catch(() => {})
    await page.keyboard.press('Escape').catch(() => {})
  }

  // Edit / Update value / Dispose via first card's detail rail
  const firstCard = page.locator('.as-card').filter({ hasText: 'Marina Rise' }).first()
  if (await firstCard.count()) {
    await firstCard.click().catch(() => {})
    await page.waitForTimeout(400)

    const updateBtn = page.locator('button:has-text("Update value")').first()
    if (await updateBtn.count()) {
      await updateBtn.click().catch(() => {})
      await page.waitForTimeout(300)
      await page.screenshot({ path: path.join(dir, 'update-value-modal.png') })
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
        await page.screenshot({ path: path.join(dir, 'edit-modal.png') })
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
        await page.screenshot({ path: path.join(dir, 'dispose-modal.png') })
        await page.keyboard.press('Escape').catch(() => {})
      }
    }
  }
  await page.close()
}

await shootPrototype()
await browser.close()
console.log('done')
