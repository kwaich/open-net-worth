// CMD-01 — deterministic, production-build crypto market-data acceptance.
//
// This intentionally drives a disposable backend through its public QA scenario
// controls and then the real resolve/asset/settings browser surfaces. It does not
// intercept fetch or manufacture DOM: the only deterministic boundary is upstream
// provider data. See docs/qa/acceptance/crypto-market-data-failover.md.

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { assertSafeBoot, bootDisposableInstance, reserveFreeLoopbackPort } from './boot.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(HERE, '..', '..')
const OUT_DIR = resolve(REPO_ROOT, 'qa/artifacts/crypto-market-data-failover')
const DEMO_KEY = 'cmd01-demo-key-must-never-render'

mkdirSync(OUT_DIR, { recursive: true })

function buildFreshProductionBundle() {
  const result = spawnSync('npm', ['run', 'build'], {
    cwd: join(REPO_ROOT, 'frontend'),
    stdio: 'inherit',
  })
  if (result.status !== 0) throw new Error('CMD-01 cannot run against a stale or failed production build')
}

async function selectPort() {
  const requested = process.env.ONW_CRYPTO_MARKET_DATA_PORT
  if (!requested) return reserveFreeLoopbackPort()
  const port = Number(requested)
  assert.ok(
    Number.isInteger(port) && port >= 10_000 && port <= 65_535,
    `CMD-01 port must be a non-privileged high port, got ${requested}`,
  )
  return port
}

async function api(baseUrl, path, options = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
    ...options,
  })
  const text = await response.text()
  let body
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = text
  }
  assert.equal(response.ok, true, `${options.method ?? 'GET'} ${path} failed ${response.status}: ${text}`)
  return { body, text }
}

function checkSeries(series, { state, provider, minPoints = 0 }) {
  assert.equal(series.state, state, `expected series state=${state}, got ${series.state}`)
  assert.equal(series.provider, provider, `expected provider=${provider}, got ${series.provider}`)
  assert.ok(Array.isArray(series.points), 'series points must be an array')
  assert.ok(series.points.length >= minPoints, `expected at least ${minPoints} real points, got ${series.points.length}`)
  if (minPoints >= 2) assert.ok(series.as_of, 'drawable series must expose provider freshness')
}

async function setScenario(baseUrl, name, clearPersistent = false) {
  await api(baseUrl, '/api/qa/market-data/scenario', {
    method: 'POST', body: JSON.stringify({ name, clear_persistent: clearPersistent }),
  })
}

async function status(baseUrl) {
  return (await api(baseUrl, '/api/qa/market-data/status')).body
}

async function setScenarioAfterProviderDrain(baseUrl, name) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await setScenario(baseUrl, name)
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 100))
    const snapshot = await status(baseUrl)
    const quiet = snapshot.requests.length === 0
      && Object.values(snapshot.calls).every((count) => count === 0)
    if (quiet) return
  }
  throw new Error(`provider traffic did not drain before scenario ${name}`)
}

function checkCounts(snapshot, expected) {
  for (const [provider, count] of Object.entries(expected)) {
    assert.equal(snapshot.calls[provider], count, `expected ${provider} calls=${count}, got ${snapshot.calls[provider]}`)
  }
}

function checkProviderOrder(snapshot, expected, message) {
  assert.deepEqual(
    snapshot.requests.map((request) => request.provider),
    expected,
    message,
  )
}

async function gotoAsset(page, baseUrl, instrumentId) {
  await page.goto(`${baseUrl}/asset/${encodeURIComponent(instrumentId)}`, { waitUntil: 'domcontentloaded' })
  await page.locator('.ad-chart-wrap').waitFor({ state: 'visible', timeout: 20_000 })
}

async function waitForText(page, text) {
  try {
    await page.getByText(text, { exact: false }).first().waitFor({ state: 'visible', timeout: 20_000 })
  } catch (error) {
    const chartText = await page.locator('.ad-chart-wrap').allTextContents()
    throw new Error(`missing visible ${JSON.stringify(text)}; chart text: ${chartText.join(' | ')}`, { cause: error })
  }
}

async function assertNoChartDrawing(page) {
  await waitForText(page, 'Price history unavailable')
  await page.getByRole('button', { name: 'Try again' }).waitFor({ state: 'visible' })
  // EmptyState has its own SVG icon. A chart draws into `.ad-chart-lw`/canvas;
  // that icon is not synthetic market history and must remain available here.
  const drawingCount = await page.locator('.ad-chart-wrap .ad-chart-lw, .ad-chart-wrap .ad-chart-lw path, .ad-chart-wrap canvas, .ad-chart-wrap .ad-chart-tip').count()
  assert.equal(drawingCount, 0, `unavailable crypto state rendered ${drawingCount} chart drawing nodes`)
}

async function assertNoHorizontalOverflow(page, label) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  assert.ok(overflow <= 1, `${label} overflows horizontally by ${overflow}px`)
}

async function openSettingsData(page) {
  const mobile = await page.evaluate(() => matchMedia('(max-width: 639.98px)').matches)
  if (mobile) {
    await page.getByRole('button', { name: 'More', exact: true }).click()
    await page.locator('.mb-sheet').getByRole('button', { name: 'Settings', exact: true }).click()
  } else {
    const settingsButton = page.locator('.sidebar').getByRole('button', { name: 'Settings', exact: true })
    const settingsButtonOnScreen = await settingsButton.evaluate((element) => {
      const rect = element.getBoundingClientRect()
      return rect.right > 0 && rect.left < innerWidth && rect.bottom > 0 && rect.top < innerHeight
    })
    if (!settingsButtonOnScreen) {
      await page.getByRole('button', { name: 'Open navigation menu', exact: true }).click()
      await page.locator('.sidebar.drawer-open').waitFor({ state: 'visible', timeout: 20_000 })
      await page.waitForFunction(() => {
        const drawer = document.querySelector('.sidebar.drawer-open')
        if (!drawer) return false
        const rect = drawer.getBoundingClientRect()
        return rect.left >= -1 && rect.right > 0
      }, undefined, { timeout: 20_000 })
    }
    await settingsButton.click()
  }
  const settings = page.locator('.onw-settings')
  await settings.waitFor({ state: 'visible', timeout: 20_000 })
  if (mobile) {
    await settings.locator('.rd-set-menu').getByRole('button', { name: 'Data', exact: true }).click()
  } else {
    await settings.locator('.rd-set-nav').getByRole('button', { name: 'Data', exact: true }).click()
  }
  await page.getByText('Market data', { exact: true }).waitFor({ state: 'visible', timeout: 20_000 })
  await page.getByLabel('CoinGecko Demo API key').waitFor({ state: 'visible', timeout: 20_000 })
}

async function assertLuciusDoesNotCoverMarketData(page, target, label) {
  try {
    await page.waitForFunction((selector) => {
      const element = document.querySelector(selector)
      const shell = document.querySelector('.app-shell.chat-open')
      const panel = shell?.querySelector('.lx-side.on')
      if (!element || !panel || !shell) return false
      const targetRect = element.getBoundingClientRect()
      const panelRect = panel.getBoundingClientRect()
      const shellRect = shell.getBoundingClientRect()
      const intersects = !(
        targetRect.right <= panelRect.left
        || targetRect.left >= panelRect.right
        || targetRect.bottom <= panelRect.top
        || targetRect.top >= panelRect.bottom
      )
      return !intersects && shellRect.right <= panelRect.left + 1
    }, target, { timeout: 5_000 })
  } catch (error) {
    const geometry = await page.evaluate((selector) => {
      const element = document.querySelector(selector)
      const shell = document.querySelector('.app-shell.chat-open')
      const panel = shell?.querySelector('.lx-side.on')
      const root = document.documentElement
      return {
        target: element?.getBoundingClientRect().toJSON() ?? null,
        panel: panel?.getBoundingClientRect().toJSON() ?? null,
        shell: shell?.getBoundingClientRect().toJSON() ?? null,
        chatWidth: getComputedStyle(root).getPropertyValue('--chat-width'),
        viewport: { width: innerWidth, height: innerHeight },
      }
    }, target)
    throw new Error(`${label}: Lucius clearance did not settle (${JSON.stringify(geometry)})`, { cause: error })
  }
  const result = await page.evaluate((selector) => {
    const element = document.querySelector(selector)
    const shell = document.querySelector('.app-shell.chat-open')
    const panel = shell?.querySelector('.lx-side.on')
    if (!element || !panel) return { present: false, covered: true }
    const targetRect = element.getBoundingClientRect()
    const panelRect = panel.getBoundingClientRect()
    const shellRect = shell?.getBoundingClientRect()
    const intersects = !(
      targetRect.right <= panelRect.left
      || targetRect.left >= panelRect.right
      || targetRect.bottom <= panelRect.top
      || targetRect.top >= panelRect.bottom
    )
    return {
      present: true,
      covered: intersects,
      shellRight: shellRect?.right ?? null,
      panelLeft: panelRect.left,
    }
  }, target)
  assert.equal(result.present, true, `${label}: market-data target or Lucius panel is absent`)
  assert.equal(
    result.covered,
    false,
    `${label}: Lucius covers the market-data target (${JSON.stringify(result)})`,
  )
  assert.ok(
    result.shellRight === null || result.shellRight <= result.panelLeft + 1,
    `${label}: app shell paints beneath Lucius (${JSON.stringify(result)})`,
  )
}

async function captureLuciusMatrix(page, app, bitcoinId) {
  for (const width of [640, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await setScenario(app.baseUrl, 'yahoo_live')
    await gotoAsset(page, app.baseUrl, bitcoinId)
    await waitForText(page, 'Yahoo Finance')
    assert.equal(await page.locator('.app-shell.chat-open').count(), 0, `${width}px chart must start chat-closed`)
    await assertNoHorizontalOverflow(page, `${width}px chat-closed chart`)
    await page.screenshot({ path: join(OUT_DIR, `CMD-01-chart-chat-closed-${width}.png`), fullPage: false })

    await openSettingsData(page)
    await page.locator('.rd-panel').filter({ hasText: 'Market data' }).first().waitFor({ state: 'visible', timeout: 20_000 })
    await assertNoHorizontalOverflow(page, `${width}px chat-closed Settings`)
    await page.screenshot({ path: join(OUT_DIR, `CMD-01-settings-chat-closed-${width}.png`), fullPage: false })
    await page.getByRole('button', { name: 'Close settings' }).click()
    await page.locator('.onw-settings-overlay').waitFor({ state: 'detached', timeout: 20_000 })

    await gotoAsset(page, app.baseUrl, bitcoinId)
    await waitForText(page, 'Yahoo Finance')

    await page.locator('#chat-toggle-btn').click()
    await page.locator('.lx-side.on').waitFor({ state: 'visible', timeout: 20_000 })
    await assertLuciusDoesNotCoverMarketData(page, '.ad-chart-wrap', `${width}px chart`)
    await assertNoHorizontalOverflow(page, `${width}px chat-open chart`)
    await page.screenshot({ path: join(OUT_DIR, `CMD-01-chart-chat-open-${width}.png`), fullPage: false })

    // Settings is a workspace modal and correctly owns pointer events over the page.
    // Desktop Lucius persists across navigation, so open it first and prove that the
    // real Settings overlay insets beside the already-open panel.
    await openSettingsData(page)
    const marketPanel = page.locator('.rd-panel').filter({ hasText: 'Market data' }).first()
    await marketPanel.waitFor({ state: 'visible', timeout: 20_000 })
    await page.locator('.lx-side.on').waitFor({ state: 'visible', timeout: 20_000 })
    await assertLuciusDoesNotCoverMarketData(page, '.rd-market-row', `${width}px Settings`)
    await assertNoHorizontalOverflow(page, `${width}px chat-open Settings`)
    await page.screenshot({ path: join(OUT_DIR, `CMD-01-settings-chat-open-${width}.png`), fullPage: false })
    await page.getByRole('button', { name: 'Close settings' }).click()
    await page.locator('.onw-settings-overlay').waitFor({ state: 'detached', timeout: 20_000 })
    await page.locator('.lx-side.on button[aria-label="Collapse"]').click()
    await page.locator('.lx-side.on').waitFor({ state: 'detached', timeout: 20_000 })
  }
}

async function main() {
  const port = await selectPort()
  assert.notEqual(port, 8080, 'CMD-01 must never boot on the live dev port')
  buildFreshProductionBundle()
  const app = await bootDisposableInstance({ port })
  let browser
  try {
    assertSafeBoot({ port, dataDir: app.dataDir, cacheDir: app.cacheDir, logDir: app.logDir })
    browser = await chromium.launch({ headless: true })
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    const page = await context.newPage()
    const cmdEndpointFailures = []
    page.on('response', (response) => {
      const path = new URL(response.url()).pathname
      const inCmdSurface = path.startsWith('/api/intel/assets/')
        || path.startsWith('/api/market-data/')
        || path.startsWith('/api/qa/market-data/')
      if (inCmdSurface && response.status() >= 500) {
        cmdEndpointFailures.push(`${response.status()} ${path}`)
      }
    })

    // The normal find-or-create endpoint receives canonical CoinGecko identity;
    // Yahoo's retrieval alias must never replace it.
    const bitcoinResolve = (await api(app.baseUrl, '/api/intel/assets/resolve', {
      method: 'POST',
      body: JSON.stringify({ q: 'Bitcoin', asset_class: 'crypto', price_source: 'coingecko', price_source_id: 'bitcoin', name: 'Bitcoin' }),
    })).body
    assert.ok(bitcoinResolve.instrument_id, 'resolve must create/find canonical Bitcoin')
    const bitcoinId = bitcoinResolve.instrument_id
    const bitcoinDetail = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}`)).body
    assert.equal(bitcoinDetail.asset?.asset_class, 'crypto', 'Bitcoin must remain a crypto instrument')

    // Explicit PEPE alias contract: resolution preserves CoinGecko canonical identity.
    const pepeResolve = (await api(app.baseUrl, '/api/intel/assets/resolve', {
      method: 'POST',
      body: JSON.stringify({ q: 'PEPE', asset_class: 'crypto', price_source: 'coingecko', price_source_id: 'pepe', name: 'Pepe' }),
    })).body
    const pepeDetail = (await api(app.baseUrl, `/api/intel/assets/${pepeResolve.instrument_id}`)).body
    assert.equal(pepeDetail.asset?.asset_class, 'crypto', 'PEPE must remain a crypto instrument')
    assert.equal(String(pepeDetail.asset?.ticker).toLowerCase(), 'pepe', 'PEPE display identity must not become a Yahoo pair')
    assert.doesNotMatch(JSON.stringify(pepeDetail), /PEPE24478-USD/, 'Yahoo retrieval alias must not leak into canonical asset identity')

    await setScenario(app.baseUrl, 'yahoo_live', true)
    const yahooLive = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=1D`)).body
    checkSeries(yahooLive, { state: 'live', provider: 'yahoo', minPoints: 3 })
    // This public series request proves the configured PEPE Yahoo retrieval path
    // serves a live result while its asset/detail APIs retain canonical `pepe`.
    // The focused service regression pins the non-derivable Yahoo symbol itself:
    // `pepe` -> `PEPE24478-USD` (and never a cache key of that alias).
    const pepeYahoo = (await api(app.baseUrl, `/api/intel/assets/${pepeResolve.instrument_id}/series?timeframe=1D`)).body
    checkSeries(pepeYahoo, { state: 'live', provider: 'yahoo', minPoints: 3 })
    const yahooSnapshot = await status(app.baseUrl)
    checkCounts(yahooSnapshot, { yahoo: 2, coingecko: 0 })
    assert.ok(
      yahooSnapshot.requests.some((request) => (
        request.provider === 'yahoo'
        && request.canonical_id === 'pepe'
        && request.provider_id === 'PEPE24478-USD'
      )),
      'PEPE must request Yahoo PEPE24478-USD while retaining canonical pepe',
    )
    await gotoAsset(page, app.baseUrl, bitcoinId)
    await page.locator('.ad-chart-lw').waitFor({ state: 'visible', timeout: 20_000 })
    await waitForText(page, 'Live via Yahoo Finance')
    assert.ok(await page.locator('.ad-chart-lw').count(), 'Yahoo live requires chart container')
    await page.screenshot({ path: join(OUT_DIR, 'CMD-01-yahoo-live.png'), fullPage: true })

    await setScenario(app.baseUrl, 'yahoo_fail_cg_live')
    const cgLive = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=1D`)).body
    checkSeries(cgLive, { state: 'live', provider: 'coingecko', minPoints: 3 })
    checkCounts(await status(app.baseUrl), { yahoo: 1, coingecko: 1 })
    // The API assertion above deliberately consumes the outage circuit's one live
    // fallback probe. Reset the deterministic QA scenario so the browser request owns
    // its own first probe; a second production request would correctly serve saved data
    // instead of repeatedly consuming CoinGecko fallback quota.
    await setScenario(app.baseUrl, 'yahoo_fail_cg_live')
    await page.reload({ waitUntil: 'domcontentloaded' })
    await page.locator('.ad-chart-lw').waitFor({ state: 'visible', timeout: 20_000 })
    await waitForText(page, 'Live via CoinGecko')

    await setScenario(app.baseUrl, 'yahoo_fail_cg_429')
    const after429 = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=1D`)).body
    checkSeries(after429, { state: 'stale', provider: 'coingecko', minPoints: 3 })
    assert.deepEqual(
      after429.points,
      cgLive.points,
      'CoinGecko 429 after a prior success must preserve the complete last-good chart',
    )
    checkProviderOrder(
      await status(app.baseUrl),
      ['yahoo', 'coingecko'],
      'Yahoo failure followed by CoinGecko 429 must serve persisted stale data',
    )

    await setScenario(app.baseUrl, 'delayed_yahoo_fail_cg_live')
    const concurrentSeries = await Promise.all([
      api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=5D&probe=one`),
      api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=5D&probe=two`),
    ])
    for (const response of concurrentSeries) {
      checkSeries(response.body, { state: 'live', provider: 'coingecko', minPoints: 3 })
    }
    checkCounts(await status(app.baseUrl), { yahoo: 1, coingecko: 1 })

    await setScenario(app.baseUrl, 'malformed_both', true)
    const malformed = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=1D`)).body
    checkSeries(malformed, { state: 'unavailable', provider: null })
    assert.deepEqual(malformed.points, [], 'malformed provider payload must never become chart data')

    // Restore a valid fallback series for the persistent stale/unavailable state checks.
    await setScenario(app.baseUrl, 'yahoo_fail_cg_live')
    const restoredFallback = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=1D`)).body
    checkSeries(restoredFallback, { state: 'live', provider: 'coingecko', minPoints: 3 })

    await setScenario(app.baseUrl, 'both_fail')
    const stale = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=1D`)).body
    checkSeries(stale, { state: 'stale', provider: 'coingecko', minPoints: 3 })
    assert.deepEqual(stale.points, cgLive.points, 'stale series must retain the complete last-good shape')
    checkCounts(await status(app.baseUrl), { yahoo: 1, coingecko: 1 })
    await page.reload({ waitUntil: 'domcontentloaded' })
    await waitForText(page, 'Saved data')
    await waitForText(page, 'Live providers are temporarily unavailable.')
    assert.ok(await page.locator('.ad-chart-lw').count(), 'saved data must still draw its chart')
    await page.screenshot({ path: join(OUT_DIR, 'CMD-01-hard-reload-stale.png'), fullPage: true })

    await setScenario(app.baseUrl, 'both_fail', true)
    const unavailable = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=1D`)).body
    checkSeries(unavailable, { state: 'unavailable', provider: null })
    assert.deepEqual(unavailable.points, [], 'unavailable must not contain synthetic history')
    checkCounts(await status(app.baseUrl), { yahoo: 1, coingecko: 1 })
    await page.reload({ waitUntil: 'domcontentloaded' })
    await assertNoChartDrawing(page)
    await page.screenshot({ path: join(OUT_DIR, 'CMD-01-unavailable.png'), fullPage: true })

    await setScenario(app.baseUrl, 'recovered')
    const recovered = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=1D`)).body
    checkSeries(recovered, { state: 'live', provider: 'yahoo', minPoints: 3 })
    checkCounts(await status(app.baseUrl), { yahoo: 1, coingecko: 0 })

    await setScenario(app.baseUrl, 'metadata_fail')
    // Detail uses the real CoinGecko facts/metadata seam, while refresh is the
    // actual asset-screen POST path. Both must survive an upstream metadata
    // failure and must never make the healthy Yahoo price tape look unavailable.
    const metadataDetail = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}`)).body
    assert.equal(metadataDetail.asset?.asset_class, 'crypto', 'metadata failure must leave asset detail readable')
    await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/refresh?force=true`, { method: 'POST' })
    const metadataIsolated = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=1D`)).body
    checkSeries(metadataIsolated, { state: 'live', provider: 'yahoo', minPoints: 3 })
    const metadataSnapshot = await status(app.baseUrl)
    assert.ok(metadataSnapshot.calls.metadata > 0, 'metadata_fail must reach the real metadata provider path')

    await openSettingsData(page)
    const keyInput = page.getByLabel('CoinGecko Demo API key')
    const saveKey = page.getByRole('button', { name: 'Save key' })
    assert.equal(await saveKey.isDisabled(), true, 'blank Demo key save must be disabled')
    const keySaveResponse = page.waitForResponse((response) => (
      response.request().method() === 'PUT'
      && new URL(response.url()).pathname === '/api/market-data/config'
    ))
    await keyInput.fill(DEMO_KEY)
    await saveKey.click()
    const keySaveText = await (await keySaveResponse).text()
    assert.doesNotMatch(keySaveText, new RegExp(DEMO_KEY), 'Demo key leaked from PUT response')
    await page.getByText('Demo API key saved', { exact: true }).waitFor({ state: 'visible' })
    const publicConfig = await api(app.baseUrl, '/api/market-data/config')
    assert.equal(publicConfig.body.has_coingecko_demo_key, true, 'public API only confirms key presence')
    assert.doesNotMatch(publicConfig.text, new RegExp(DEMO_KEY), 'public config leaked Demo key')
    assert.equal(await keyInput.inputValue(), '', 'saved Demo key must be cleared from the browser field')
    assert.doesNotMatch(await page.locator('body').innerText(), new RegExp(DEMO_KEY), 'Demo key leaked into the rendered DOM')
    await page.screenshot({ path: join(OUT_DIR, 'CMD-01-settings-demo-key.png'), fullPage: true })

    const yahooToggle = page.getByRole('switch', { name: 'Use Yahoo Finance first for supported major crypto' })
    await yahooToggle.click()
    await waitForText(page, 'Rollback active')
    await page.reload({ waitUntil: 'domcontentloaded' })
    await page.getByText('Rollback active', { exact: true }).waitFor({ state: 'visible' })
    assert.equal(await yahooToggle.getAttribute('aria-checked'), 'false', 'rollback must persist after reload')
    // The real asset remains mounted underneath Settings and may refresh its chart/quote.
    // Park the browser while measuring exact provider order through the public API so those
    // legitimate background requests cannot consume this deterministic scenario's counters.
    await page.goto('about:blank')
    await setScenarioAfterProviderDrain(app.baseUrl, 'yahoo_fail_cg_live')
    const rollbackSeries = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=1D`)).body
    checkSeries(rollbackSeries, { state: 'live', provider: 'coingecko', minPoints: 3 })
    checkCounts(await status(app.baseUrl), { yahoo: 0, coingecko: 1 })
    // The fixture has no CoinGecko-fails/Yahoo-live state. Its both-fail state
    // still proves the inverse rollback cascade: CoinGecko is attempted first,
    // then Yahoo, before the already-persisted series is honestly served stale.
    await setScenario(app.baseUrl, 'both_fail')
    const rollbackStale = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=1D`)).body
    checkSeries(rollbackStale, { state: 'stale', provider: 'coingecko', minPoints: 3 })
    const rollbackFailureSnapshot = await status(app.baseUrl)
    checkCounts(rollbackFailureSnapshot, { yahoo: 1, coingecko: 1 })
    checkProviderOrder(
      rollbackFailureSnapshot,
      ['coingecko', 'yahoo'],
      'Rollback failure must attempt CoinGecko before Yahoo',
    )
    await setScenario(app.baseUrl, 'recovered')
    await gotoAsset(page, app.baseUrl, bitcoinId)
    await openSettingsData(page)
    const restoreToggle = page.getByRole('switch', { name: 'Use Yahoo Finance first for supported major crypto' })
    const restoreConfigResponse = page.waitForResponse((response) => (
      response.request().method() === 'PUT'
      && new URL(response.url()).pathname === '/api/market-data/config'
    ))
    await restoreToggle.click()
    await page.getByText('Rollback active', { exact: true }).waitFor({ state: 'detached' })
    const restoreConfigBody = await (await restoreConfigResponse).json()
    assert.equal(restoreConfigBody.yahoo_primary_for_major_crypto, true, 're-enable PUT must restore Yahoo-first')
    const restoredConfig = await api(app.baseUrl, '/api/market-data/config')
    assert.equal(restoredConfig.body.yahoo_primary_for_major_crypto, true, 're-enabling must persist Yahoo-first')
    await page.goto('about:blank')
    await setScenarioAfterProviderDrain(app.baseUrl, 'yahoo_fail_cg_live')
    const restoredSeries = (await api(app.baseUrl, `/api/intel/assets/${bitcoinId}/series?timeframe=1D`)).body
    checkSeries(restoredSeries, { state: 'live', provider: 'coingecko', minPoints: 3 })
    const restoredSnapshot = await status(app.baseUrl)
    checkCounts(restoredSnapshot, { yahoo: 1, coingecko: 1 })
    checkProviderOrder(
      restoredSnapshot,
      ['yahoo', 'coingecko'],
      'Yahoo-first restoration must attempt Yahoo before CoinGecko',
    )

    const telemetry = (await api(app.baseUrl, '/api/market-data/status')).body.telemetry_24h
    assert.ok(telemetry, 'public market-data status must expose bounded 24-hour soak counters')
    assert.ok(telemetry.provider_calls.yahoo >= 1, 'soak counters must record Yahoo calls')
    assert.ok(telemetry.provider_calls.coingecko >= 1, 'soak counters must record CoinGecko calls')
    assert.ok(telemetry.http_429s >= 1, 'soak counters must record the deterministic CG 429')
    assert.ok(telemetry.fallbacks >= 1, 'soak counters must record provider fallback')
    assert.ok(telemetry.stale_serves >= 1, 'soak counters must record persisted stale use')
    assert.ok(telemetry.unavailable_responses >= 1, 'soak counters must record unavailable data')
    assert.ok(telemetry.coalesced_requests >= 1, 'soak counters must record request coalescing')

    await captureLuciusMatrix(page, app, bitcoinId)

    await page.setViewportSize({ width: 390, height: 844 })
    await setScenario(app.baseUrl, 'both_fail')
    await page.goto(`${app.baseUrl}/asset/${encodeURIComponent(bitcoinId)}`, { waitUntil: 'domcontentloaded' })
    await waitForText(page, 'Saved data')
    await assertNoHorizontalOverflow(page, '390px stale chart')
    await page.screenshot({ path: join(OUT_DIR, 'CMD-01-mobile-stale.png'), fullPage: true })
    await setScenario(app.baseUrl, 'both_fail', true)
    await page.reload({ waitUntil: 'domcontentloaded' })
    await assertNoChartDrawing(page)
    await assertNoHorizontalOverflow(page, '390px unavailable chart')
    await page.screenshot({ path: join(OUT_DIR, 'CMD-01-mobile-unavailable.png'), fullPage: true })
    await openSettingsData(page)
    await assertNoHorizontalOverflow(page, '390px Market data settings')
    const controlsStack = await page.evaluate(() => {
      const key = document.querySelector('.rd-market-key-control')
      if (!key) return false
      return getComputedStyle(key).flexDirection === 'column'
    })
    assert.equal(controlsStack, true, 'mobile Demo key controls must stack vertically')
    await page.screenshot({ path: join(OUT_DIR, 'CMD-01-mobile-settings-demo-key.png'), fullPage: true })

    assert.deepEqual(cmdEndpointFailures, [], `CMD-01 endpoint failures: ${cmdEndpointFailures.join(' | ')}`)
    await context.close()
    console.log(`crypto-market-data-e2e: PASS (${OUT_DIR})`)
  } finally {
    await browser?.close()
    app.stop()
  }
}

await main()
