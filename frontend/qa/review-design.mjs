import { chromium } from 'playwright'
import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'

const out = '/tmp/onw-review-redesign'
await mkdir(out, { recursive: true })
const browser = await chromium.launch()
try {
  for (const [name, width, height] of [
    ['mobile', 390, 844],
    ['compact', 375, 667],
    ['desktop', 1440, 900],
  ]) {
    const page = await browser.newPage({ viewport: { width, height } })
    await page.goto('http://127.0.0.1:8098/qa/review-design.html')
    await page.getByRole('heading', { name: 'Northside Market' }).click()
    await page.getByRole('button', { name: 'Edit', exact: true }).click()
    await page.getByRole('button', { name: 'Done', exact: true }).click()
    await page.locator('.acct-review-card.is-editing').waitFor({ state: 'hidden' })
    const card = page.locator('.acct-review-card')
    const rect = await card.boundingBox()
    const actions = await page.locator('.acct-review-actions').boundingBox()
    const hint = await page.locator('.acct-review-swipe-hint').boundingBox()
    assert(rect.y >= 0 && rect.y + rect.height < hint.y, `${name}: card overlaps hint`)
    assert(actions.y + actions.height <= height, `${name}: actions clipped`)
    assert(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      `${name}: horizontal overflow`,
    )
    await page.screenshot({ path: `${out}/${name}.png` })
    await page.getByRole('button', { name: 'Other', exact: true }).click()
    await page.getByRole('button', { name: 'Transport', exact: true }).click()
    await page.getByRole('button', { name: 'Edit', exact: true }).click()
    assert.equal(await page.getByLabel('Category', { exact: true }).inputValue(), 'transport')
    await page.getByLabel('Memo', { exact: true }).scrollIntoViewIfNeeded()
    assert((await page.locator('.acct-review-actions').boundingBox()).y + actions.height <= height)
    await page.screenshot({ path: `${out}/${name}-edit.png` })
    await page.getByRole('button', { name: 'Done', exact: true }).click()
    await page.locator('.acct-review-card.is-editing').waitFor({ state: 'hidden' })
    await page.getByRole('heading', { name: 'Northside Market' }).scrollIntoViewIfNeeded()
    const after = await card.boundingBox()
    await page.mouse.move(after.x + after.width / 2, after.y + 90)
    await page.mouse.down()
    await page.mouse.move(after.x + after.width / 2 + 130, after.y + 90, { steps: 12 })
    await page.mouse.up()
    await page.getByRole('heading', { name: 'City Transit' }).waitFor()
    console.log(`${name}: layout, category edit, field scrolling and approve swipe passed`)
    await page.close()
  }
} finally {
  await browser.close()
}
