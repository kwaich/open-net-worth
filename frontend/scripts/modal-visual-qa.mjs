// Deterministic rendered-geometry evidence for RM-01. Boots only a disposable,
// demo-seeded instance and writes screenshots to the requested output directory.
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { bootDisposableInstance } from '../qa/boot.mjs'

const outArg = process.argv.indexOf('--out-dir')
const outDir = resolve(outArg >= 0 ? process.argv[outArg + 1] : '../docs/qa/evidence/task-205')
mkdirSync(outDir, { recursive: true })

const instance = await bootDisposableInstance({ port: 8094 })
let browser

try {
  browser = await chromium.launch()
  const checks = [
    { name: 'mobile-compact', viewport: { width: 320, height: 568 }, mobile: true },
    { name: 'mobile', viewport: { width: 390, height: 844 }, mobile: true },
    { name: 'desktop', viewport: { width: 1440, height: 900 } },
  ]

  for (const check of checks) {
    const context = await browser.newContext({ viewport: check.viewport })
    const page = await context.newPage()
    page.setDefaultTimeout(15000)
    await page.goto(`${instance.baseUrl}/?gallery=1`, { waitUntil: 'domcontentloaded' })
    const opener = page.getByRole('button', { name: 'Open xl', exact: true })
    await opener.scrollIntoViewIfNeeded()
    await opener.click({ force: true })
    const dialog = page.getByRole('dialog', { name: /Extra-large modal/ })
    await dialog.waitFor()
    await page.waitForTimeout(250)

    if (check.mobile) {
      await dialog.locator('.ui-modal__body').evaluate((body) => {
        for (let index = 0; index < 30; index += 1) {
          const row = document.createElement('p')
          row.textContent = `Overflow regression row ${index + 1}`
          row.dataset.modalOverflowRow = String(index + 1)
          body.append(row)
        }
      })
    }

    const geometry = await dialog.evaluate((node) => {
      const rect = node.getBoundingClientRect()
      const footer = node.querySelector('.ui-modal__footer')?.getBoundingClientRect()
      const body = node.querySelector('.ui-modal__body')
      const lastRow = node.querySelector('[data-modal-overflow-row="30"]')
      if (body && lastRow) body.scrollTop = body.scrollHeight
      const bodyRect = body?.getBoundingClientRect()
      const lastRowRect = lastRow?.getBoundingClientRect()
      return {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        width: rect.width,
        footerBottom: footer?.bottom,
        bodyScrollTop: body?.scrollTop,
        bodyScrollHeight: body?.scrollHeight,
        bodyClientHeight: body?.clientHeight,
        lastRowBottom: lastRowRect?.bottom,
        bodyBottom: bodyRect?.bottom,
        viewportWidth: innerWidth,
        viewportHeight: innerHeight,
        scrollLocked: document.body.hasAttribute('data-scroll-locked'),
      }
    })

    if (!geometry.scrollLocked) throw new Error(`${check.name}: document was not scroll locked`)
    if (geometry.left < -1 || geometry.right > geometry.viewportWidth + 1) {
      throw new Error(`${check.name}: dialog exceeds viewport horizontally`)
    }
    if (geometry.top < -1 || geometry.bottom > geometry.viewportHeight + 1) {
      throw new Error(
        `${check.name}: dialog exceeds viewport vertically ${JSON.stringify(geometry)}`,
      )
    }
    if (check.mobile) {
      if (Math.abs(geometry.bottom - geometry.viewportHeight) > 1) {
        throw new Error(`${check.name}: dialog is not bottom docked`)
      }
      if (!geometry.footerBottom || geometry.footerBottom > geometry.bottom) {
        throw new Error(`${check.name}: action footer is not retained inside the dialog edge`)
      }
      if (
        !geometry.bodyScrollTop ||
        !geometry.bodyScrollHeight ||
        !geometry.bodyClientHeight ||
        geometry.bodyScrollHeight <= geometry.bodyClientHeight ||
        !geometry.lastRowBottom ||
        !geometry.bodyBottom ||
        geometry.lastRowBottom > geometry.bodyBottom + 1
      ) {
        throw new Error(
          `${check.name}: long modal body cannot scroll fully ${JSON.stringify(geometry)}`,
        )
      }
    } else if (Math.abs((geometry.left + geometry.right) / 2 - geometry.viewportWidth / 2) > 2) {
      throw new Error('desktop: dialog is not horizontally centered')
    }

    await page.screenshot({
      path: `${outDir}/RM-01-${check.name}.png`,
      fullPage: false,
    })
    console.log(`PASS RM-01 ${check.name}`, geometry)
    await context.close()
  }

  const mobileFlows = [
    { name: 'more', route: '/overview', button: 'More', dialog: /^More$/ },
    {
      name: 'overview',
      route: '/overview',
      before: 'Customize overview',
      button: 'Add section',
      dialog: /^Add section$/,
    },
    {
      name: 'plan',
      route: '/plan',
      button: 'FIRE Settings',
      dialog: /^FIRE Settings$/,
      bodySelector: '.pl-modal-body',
      footerSelector: '.pl-modal-foot',
    },
    {
      name: 'commitments',
      route: '/commitments',
      button: 'New commitment',
      dialog: /^New commitment$/,
    },
  ]
  for (const flow of mobileFlows) {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
    const page = await context.newPage()
    page.setDefaultTimeout(20000)
    await page.goto(`${instance.baseUrl}/#${flow.route}`, { waitUntil: 'domcontentloaded' })
    if (flow.before) await page.getByRole('button', { name: flow.before, exact: true }).click()
    const opener = page.getByRole('button', { name: flow.button, exact: true }).first()
    await opener.waitFor()
    await opener.click()
    const dialog = page.getByRole('dialog', { name: flow.dialog }).last()
    await dialog.waitFor()
    await page.waitForTimeout(250)
    const rect = await dialog.evaluate((node, flow) => {
      const r = node.getBoundingClientRect()
      const overlay = node.parentElement
      const tabBar = document.querySelector('.mb-tabbar')
      const body = flow.bodySelector ? node.querySelector(flow.bodySelector) : null
      const footer = flow.footerSelector ? node.querySelector(flow.footerSelector) : null
      if (body) body.scrollTop = body.scrollHeight
      const lastBodyChild = body?.lastElementChild?.getBoundingClientRect()
      const bodyRect = body?.getBoundingClientRect()
      const footerRect = footer?.getBoundingClientRect()
      return {
        left: r.left,
        right: r.right,
        top: r.top,
        bottom: r.bottom,
        viewportWidth: innerWidth,
        viewportHeight: innerHeight,
        scrollLocked: document.body.hasAttribute('data-scroll-locked'),
        activeInside: node.contains(document.activeElement),
        overlayZ: overlay ? Number.parseInt(getComputedStyle(overlay).zIndex, 10) : null,
        tabBarZ: tabBar ? Number.parseInt(getComputedStyle(tabBar).zIndex, 10) : null,
        bodyScrollTop: body?.scrollTop,
        bodyScrollHeight: body?.scrollHeight,
        bodyClientHeight: body?.clientHeight,
        lastBodyChildBottom: lastBodyChild?.bottom,
        bodyBottom: bodyRect?.bottom,
        footerBottom: footerRect?.bottom,
        tabBarTop: tabBar?.getBoundingClientRect().top,
      }
    }, flow)
    if (
      rect.left < -1 ||
      rect.right > rect.viewportWidth + 1 ||
      rect.top < -1 ||
      rect.bottom > rect.viewportHeight + 1
    ) {
      throw new Error(`${flow.name}: dialog exceeds mobile viewport ${JSON.stringify(rect)}`)
    }
    if (!rect.scrollLocked || !rect.activeInside) {
      throw new Error(`${flow.name}: missing scroll lock or initial focus ${JSON.stringify(rect)}`)
    }
    if (
      flow.bodySelector &&
      (!rect.bodyScrollTop ||
        !rect.bodyScrollHeight ||
        !rect.bodyClientHeight ||
        rect.bodyScrollHeight <= rect.bodyClientHeight ||
        !rect.lastBodyChildBottom ||
        !rect.bodyBottom ||
        rect.lastBodyChildBottom > rect.bodyBottom + 1)
    ) {
      throw new Error(`${flow.name}: long modal body cannot scroll fully ${JSON.stringify(rect)}`)
    }
    if (
      flow.footerSelector &&
      (rect.overlayZ == null ||
        rect.tabBarZ == null ||
        rect.overlayZ <= rect.tabBarZ ||
        !rect.footerBottom)
    ) {
      throw new Error(`${flow.name}: footer is obscured by the mobile tab bar ${JSON.stringify(rect)}`)
    }
    await page.screenshot({ path: `${outDir}/RM-01-${flow.name}-mobile.png` })
    console.log(`PASS RM-01 ${flow.name}`, rect)
    await context.close()
  }
} finally {
  if (browser) await browser.close()
  instance.stop()
}
