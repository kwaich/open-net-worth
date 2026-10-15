// model-telemetry-states.mjs — deterministic screenshot evidence for all three
// ModelTelemetryPanel states (empty / populated / error), via network-route
// interception on a disposable, freshly-seeded instance. Self-boots, self-tears-down.
// Usage: node qa/model-telemetry-states.mjs

import { chromium } from 'playwright'
import { mkdirSync, mkdtempSync } from 'node:fs'
import { join, resolve, dirname } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { bootDisposableInstance, reserveFreeLoopbackPort } from './boot.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT_DIR = resolve(HERE, '../../qa/artifacts/model-telemetry-screenshots')
mkdirSync(OUT_DIR, { recursive: true })

const ENDPOINT = '**/api/ai/model-telemetry'

const POPULATED_FIXTURE = {
  models: [
    {
      model: 'claude-opus-4-20250514',
      provider: 'Anthropic',
      ttft_ms_p50: 842,
      duration_ms_p50: 6100,
      tok_per_sec_p50: 38,
      error_rate: 0.06,
      errors: 3,
      samples: 47,
    },
    {
      model: 'claude-sonnet-4-5',
      provider: 'Anthropic',
      ttft_ms_p50: 310,
      duration_ms_p50: 1400,
      tok_per_sec_p50: 61,
      error_rate: 0,
      errors: 0,
      samples: 50,
    },
    {
      model: 'gpt-5-mini-override',
      provider: null,
      ttft_ms_p50: 420,
      duration_ms_p50: null,
      tok_per_sec_p50: 55,
      error_rate: 0,
      errors: 0,
      samples: 12,
    },
  ],
}

async function gotoModelSettings(page, baseUrl) {
  // Settings is a routed overlay; ?sub=aiUsage deep-links straight to the usage section
  // (Settings.tsx's SECTION_KEYS / setSec), no clicking through the rail needed.
  await page.goto(`${baseUrl}/settings?sub=aiUsage`)
  try {
    await page.waitForSelector('[data-screen-label="Settings — aiUsage"]', { timeout: 15000 })
  } catch (err) {
    const debugPath = join(OUT_DIR, 'DEBUG-navigation-failure.png')
    await page.screenshot({ path: debugPath, fullPage: true }).catch(() => {})
    const bodyText = await page.textContent('body').catch(() => '(could not read body)')
    console.error(`  navigation debug screenshot: ${debugPath}`)
    console.error(`  body text (first 500 chars): ${(bodyText || '').slice(0, 500)}`)
    throw err
  }
  await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {})
}

async function capture(browser, baseUrl, { name, routeHandler, waitFor }) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } })
  try {
    if (routeHandler) await page.route(ENDPOINT, routeHandler)
    await gotoModelSettings(page, baseUrl)
    const panel = page.locator('text=/Model performance/i')
    const ok = await panel
      .first()
      .waitFor({ timeout: 8000 })
      .then(() => true)
      .catch(() => false)
    if (ok)
      await panel
        .first()
        .scrollIntoViewIfNeeded()
        .catch(() => {})
    await page.waitForTimeout(waitFor || 400)
    const filename = join(OUT_DIR, `model-telemetry-${name}.png`)
    await page.screenshot({ path: filename, fullPage: false })
    console.log(
      `  ${ok ? '✓' : '⚠'} ${name} → ${filename}${ok ? '' : ' (panel title not found — screenshot may not show the panel)'}`,
    )
    return ok
  } finally {
    await page.close()
  }
}

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), 'onw-qa-telemetry-empty-'))
  const port = await reserveFreeLoopbackPort()
  console.log(
    `[states] booting disposable instance on :${port} with an empty external dataDir (no seed)`,
  )
  const { baseUrl, stop } = await bootDisposableInstance({ port, dataDir })
  let allOk = true
  try {
    // A fresh empty DATA_DIR is otherwise gated into the /setup onboarding wizard
    // (RouterShell.tsx's is_fresh redirect) before any tab, Settings included, is
    // reachable. Stamping onboarding complete (the same DB row the wizard itself
    // writes on finish) skips it — a book with zero accounts stays otherwise
    // untouched, so model_telemetry stays genuinely empty for the empty-state shot.
    const completeRes = await fetch(`${baseUrl}/api/setup-status/complete`, { method: 'POST' })
    console.log(`[states] marked onboarding complete: ${completeRes.status}`)

    const browser = await chromium.launch({ headless: true })
    try {
      console.log('\n=== empty state (real backend, zero telemetry samples) ===')
      allOk = (await capture(browser, baseUrl, { name: 'empty', waitFor: 600 })) && allOk

      console.log('\n=== populated state (route-mocked response) ===')
      allOk =
        (await capture(browser, baseUrl, {
          name: 'populated',
          routeHandler: (route) =>
            route.fulfill({
              status: 200,
              contentType: 'application/json',
              body: JSON.stringify(POPULATED_FIXTURE),
            }),
          waitFor: 600,
        })) && allOk

      console.log('\n=== error state (route-mocked 500) ===')
      allOk =
        (await capture(browser, baseUrl, {
          name: 'error',
          routeHandler: (route) =>
            route.fulfill({
              status: 500,
              contentType: 'application/json',
              body: JSON.stringify({ detail: 'forced QA failure' }),
            }),
          waitFor: 600,
        })) && allOk
    } finally {
      await browser.close()
    }
  } finally {
    stop()
  }
  console.log(
    allOk
      ? '\n✓ all three states captured'
      : '\n⚠ one or more states could not confirm the panel title — inspect screenshots manually',
  )
  process.exit(allOk ? 0 : 1)
}

main().catch((err) => {
  console.error('Fatal:', err)
  process.exit(2)
})
