// Real-browser regression for mobile account pickers when the software keyboard changes the
// visual viewport. The old shared picker hook forced position:fixed from layout-viewport
// measurements; on iOS the keyboard pans a separate visual viewport, detaching the menu from
// its field. Phone pickers now stay absolute inside their field wrapper and open upward.

import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { bootDisposableInstance } from './boot.mjs'

const outputDir = process.env.ONW_EVIDENCE || '/tmp/onw-mobile-picker-keyboard'
const port = Number(process.env.ONW_PORT || 8096)
mkdirSync(outputDir, { recursive: true })

const failures = []
function check(condition, message) {
  if (!condition) failures.push(message)
  console.log(`${condition ? 'PASS' : 'FAIL'} — ${message}`)
}

const app = await bootDisposableInstance({ port })
const browser = await chromium.launch({ headless: true })

try {
  const payeeSeed = await fetch(`${app.baseUrl}/api/payees`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name: 'Vietnam Airlines' }),
  })
  if (!payeeSeed.ok) throw new Error(`payee seed failed: ${payeeSeed.status}`)

  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
  })

  await context.addInitScript(() => {
    localStorage.setItem('chat-open', 'false')
    const target = new EventTarget()
    const state = { width: 390, height: 844, offsetTop: 0, offsetLeft: 0, scale: 1 }
    const viewport = {
      get width() {
        return state.width
      },
      get height() {
        return state.height
      },
      get offsetTop() {
        return state.offsetTop
      },
      get offsetLeft() {
        return state.offsetLeft
      },
      get pageTop() {
        return state.offsetTop
      },
      get pageLeft() {
        return state.offsetLeft
      },
      get scale() {
        return state.scale
      },
      addEventListener: target.addEventListener.bind(target),
      removeEventListener: target.removeEventListener.bind(target),
      dispatchEvent: target.dispatchEvent.bind(target),
      onresize: null,
      onscroll: null,
    }
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      get: () => viewport,
    })
    window.__setQaVisualViewport = (height, offsetTop) => {
      state.height = height
      state.offsetTop = offsetTop
      target.dispatchEvent(new Event('resize'))
      target.dispatchEvent(new Event('scroll'))
    }
  })

  const page = await context.newPage()
  const pageErrors = []
  const consoleErrors = []
  page.on('pageerror', (error) => pageErrors.push(String(error)))
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })

  await page.goto(`${app.baseUrl}/accounts`, { waitUntil: 'domcontentloaded' })

  const transaction = page.locator('.acct-card').first()
  await transaction.waitFor({ timeout: 10_000 })
  await transaction.click()
  await page.locator('.acct-detail.sheet').waitFor()
  await page.getByRole('button', { name: 'Edit transaction' }).click()

  const payee = page.locator('input[placeholder="Payee"]')
  await payee.scrollIntoViewIfNeeded()
  await payee.fill('Vietnam')
  const menu = page.locator('.acct-payf-pop')
  await menu.waitFor()
  await page.waitForTimeout(200)

  const geometry = async () =>
    page.evaluate(() => {
      const field = document.querySelector('input[placeholder="Payee"]')
      const popup = document.querySelector('.acct-payf-pop')
      const sheet = document.querySelector('.acct-detail.sheet')
      if (!(field && popup && sheet)) return null
      const f = field.getBoundingClientRect()
      const p = popup.getBoundingClientRect()
      const s = sheet.getBoundingClientRect()
      return {
        position: getComputedStyle(popup).position,
        fieldTop: Math.round(f.top),
        popupTop: Math.round(p.top),
        popupBottom: Math.round(p.bottom),
        popupLeft: Math.round(p.left),
        popupRight: Math.round(p.right),
        sheetTop: Math.round(s.top),
        viewportHeight: window.visualViewport?.height ?? window.innerHeight,
        viewportTop: window.visualViewport?.offsetTop ?? 0,
      }
    })

  const before = await geometry()
  check(
    before?.position === 'absolute',
    `phone payee menu uses anchor-local positioning (${before?.position})`,
  )
  check(
    Math.abs((before?.fieldTop ?? 0) - (before?.popupBottom ?? 100)) <= 5,
    `menu is attached above its field before the keyboard (gap ${(before?.fieldTop ?? 0) - (before?.popupBottom ?? 0)}px)`,
  )
  check(
    (before?.popupLeft ?? -1) >= 0 && (before?.popupRight ?? 391) <= 390,
    `menu stays within the phone width (${before?.popupLeft}..${before?.popupRight})`,
  )
  await page.screenshot({ path: join(outputDir, 'payee-before-keyboard.png') })

  await page.evaluate(() => window.__setQaVisualViewport(430, 250))
  await page.waitForTimeout(250)
  const after = await geometry()
  check(
    after?.position === 'absolute',
    `keyboard-open menu remains anchor-local (${after?.position})`,
  )
  check(
    Math.abs((after?.fieldTop ?? 0) - (after?.popupBottom ?? 100)) <= 5,
    `menu remains attached after visual viewport resize/scroll (gap ${(after?.fieldTop ?? 0) - (after?.popupBottom ?? 0)}px)`,
  )
  check(
    (after?.popupTop ?? -1) >= (after?.sheetTop ?? 0),
    `menu remains inside the transaction sheet (${after?.popupTop} >= ${after?.sheetTop})`,
  )
  await page.screenshot({ path: join(outputDir, 'payee-keyboard-open.png') })

  const firstSavedPayee = menu.locator('.acct-payf-item:not(.create)').first()
  const selectedLabel = (await firstSavedPayee.innerText()).trim()
  await firstSavedPayee.click()
  check((await menu.count()) === 0, 'tapping a saved payee closes the menu')
  check(
    (await payee.inputValue()).trim() === selectedLabel,
    `tapping a saved payee selects it (${selectedLabel})`,
  )
  check(pageErrors.length === 0, `no uncaught page errors (${pageErrors.length})`)
  check(consoleErrors.length === 0, `no console errors (${consoleErrors.length})`)

  await context.close()
} finally {
  await browser.close()
  app.stop()
}

console.log(`\nEvidence: ${outputDir}`)
if (failures.length) {
  console.error(`\nmobile-picker-keyboard: FAIL (${failures.length})`)
  for (const failure of failures) console.error(` - ${failure}`)
  process.exit(1)
}
console.log('\nmobile-picker-keyboard: PASS')
