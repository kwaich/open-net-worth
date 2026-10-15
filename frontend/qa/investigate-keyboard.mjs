// Root-cause investigation for "the nav bar does not hide when the keyboard opens".
//
//   node qa/investigate-keyboard.mjs
//
// Every previous check in this repo tested the keyboard logic against SYNTHETIC markup or a
// jsdom stub. Neither can see the real page. This boots the REAL built app in a REAL browser
// at a phone viewport and reports the state at every layer boundary:
//
//   L1  is the mobile shell even active?          (.app-shell.mb-shell)
//   L2  is the tab bar where the CSS expects it?  (descendant of .app-shell.mb-shell)
//   L3  does the hook react to focus?             (.mobile-keyboard-open on <html>)
//   L4  does the CSS then win in the LIVE DOM?    (computed display of .mb-tabbar)
//
// `window.visualViewport` is replaced with a controllable stub in an init script, BEFORE the
// app's JS runs, so the hook binds to it and a keyboard can be simulated deterministically —
// including the case where the viewport reports nothing at all, which is what the owner's
// device appears to do.

import { chromium } from 'playwright'
import { bootDisposableInstance } from './boot.mjs'

const PORT = 8094
const quiet = () => {}

const inst = await bootDisposableInstance({ port: PORT, log: quiet })
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 390, height: 844 } })

// Controllable visualViewport, installed before any app code runs.
await page.addInitScript(() => {
  const target = new EventTarget()
  const state = { width: 390, height: 844, offsetTop: 0, offsetLeft: 0, scale: 1 }
  const vv = {
    get width() { return state.width },
    get height() { return state.height },
    get offsetTop() { return state.offsetTop },
    get offsetLeft() { return state.offsetLeft },
    get pageTop() { return 0 },
    get pageLeft() { return 0 },
    get scale() { return state.scale },
    addEventListener: target.addEventListener.bind(target),
    removeEventListener: target.removeEventListener.bind(target),
    dispatchEvent: target.dispatchEvent.bind(target),
    onresize: null,
    onscroll: null,
  }
  Object.defineProperty(window, 'visualViewport', { configurable: true, get: () => vv })
  // eslint-disable-next-line no-underscore-dangle
  window.__kb = {
    setHeight(h) {
      state.height = h
      target.dispatchEvent(new Event('resize'))
    },
  }
})

await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: 'domcontentloaded' })
const barAppeared = await page
  .waitForSelector('.mb-tabbar', { timeout: 20000 })
  .then(() => true)
  .catch(() => false)
console.log(`\n   .mb-tabbar appeared within 20s: ${barAppeared}`)
await page.waitForTimeout(1500)

const probe = () =>
  page.evaluate(() => {
    const root = document.documentElement
    const shell = document.querySelector('.app-shell')
    const bar = document.querySelector('.mb-tabbar')
    const active = document.activeElement
    return {
      shellClasses: shell ? shell.className : '(no .app-shell)',
      mbShell: !!document.querySelector('.app-shell.mb-shell'),
      barInDom: !!bar,
      barInsideShell: !!document.querySelector('.app-shell.mb-shell .mb-tabbar'),
      barParentChain: bar
        ? (() => {
            const chain = []
            for (let n = bar.parentElement; n && chain.length < 6; n = n.parentElement) {
              chain.push(n.tagName.toLowerCase() + (n.className ? '.' + String(n.className).split(' ')[0] : ''))
            }
            return chain.join(' < ')
          })()
        : '-',
      htmlClasses: root.className || '(none)',
      barPosition: bar ? getComputedStyle(bar).position : '-',
      barDisplay: bar ? getComputedStyle(bar).display : '-',
      barTop: bar ? Math.round(bar.getBoundingClientRect().top) : -1,
      barHeight: bar ? Math.round(bar.getBoundingClientRect().height) : -1,
      activeEl: active ? active.tagName + (active.className ? '.' + String(active.className).split(' ')[0] : '') : '-',
    }
  })

const show = (label, p) => {
  console.log(`\n── ${label}`)
  console.log(`   L1 mb-shell active .......... ${p.mbShell}`)
  console.log(`   L2 bar in DOM / inside shell  ${p.barInDom} / ${p.barInsideShell}`)
  console.log(`      bar parents ............. ${p.barParentChain}`)
  console.log(`   L3 bar position ............ ${p.barPosition}  (must NOT be 'fixed')`)
  console.log(`   L4 bar display ............. ${p.barDisplay}  (top ${p.barTop}, h ${p.barHeight})`)
  console.log(`      activeElement ........... ${p.activeEl}`)
}

show('A. loaded, nothing focused', await probe())

// L3 is the only layer left, so drive it properly: go where the fields actually are.
const SELECTOR = [
  'input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="color"]):not([type="submit"]):not([type="button"])',
  'textarea',
  '[contenteditable="true"]',
  '.app-layout--lucius .chat-composer',
].join(',')

async function inventory(where) {
  const found = await page.evaluate((sel) => {
    const all = [...document.querySelectorAll('input,textarea,[contenteditable]')]
    return all.slice(0, 8).map((el) => ({
      tag: el.tagName,
      type: el.getAttribute('type') || '',
      cls: String(el.className).slice(0, 34),
      ce: el.getAttribute('contenteditable'),
      matchesHookSelector: el.matches(sel),
    }))
  }, SELECTOR)
  console.log(`\n   fields on ${where}: ${found.length}`)
  for (const f of found) {
    console.log(
      `     ${f.tag}${f.type ? '[' + f.type + ']' : ''}.${f.cls || '-'}` +
        `${f.ce ? ' ce=' + f.ce : ''}  → hook selector matches: ${f.matchesHookSelector}`,
    )
  }
  return found
}

async function focusFirstAndProbe(where) {
  const ok = await page.evaluate((sel) => {
    // Skip file inputs: they match the selector but cannot take focus and raise no keyboard.
    const el = [...document.querySelectorAll(sel)].find(
      (e) => !(e.tagName === 'INPUT' && e.getAttribute('type') === 'file'),
    )
    if (!el) return false
    el.focus()
    if (el.isContentEditable && document.activeElement !== el) {
      // A contenteditable sometimes needs a selection to actually take focus.
      const r = document.createRange()
      r.selectNodeContents(el)
      getSelection()?.removeAllRanges()
      getSelection()?.addRange(r)
      el.focus()
    }
    return true
  }, SELECTOR)
  if (!ok) {
    console.log(`\n   !! nothing on ${where} matches the hook's TEXT_ENTRY_SELECTOR`)
    return
  }
  await page.waitForTimeout(150)
  const landed = await page.evaluate((sel) => {
    const a = document.activeElement
    return {
      tag: a ? a.tagName : '-',
      cls: a ? String(a.className).slice(0, 30) : '-',
      matches: a ? a.matches(sel) : false,
    }
  }, SELECTOR)
  console.log(
    `\n   >> activeElement after focus: ${landed.tag}.${landed.cls} — hook sees typing=${landed.matches}`,
  )
  show(`focused a field on ${where} — viewport SILENT`, await probe())

  await page.evaluate(() => window.__kb.setHeight(510))
  await page.waitForTimeout(150)
  show(`focused a field on ${where} — viewport 844 -> 510`, await probe())

  await page.evaluate(() => window.__kb.setHeight(844))
  await page.waitForTimeout(150)
}

// --- Investments (the tab the owner named: "SEARCH BAR IN INVESTMENTS") ---
await page.evaluate(() => {
  const slot = [...document.querySelectorAll('.mb-tab')].find((b) =>
    b.textContent.toLowerCase().includes('investments'),
  )
  slot?.click()
})
await page.waitForTimeout(2500)
await inventory('Investments (before opening search)')
// The phone layout collapses search behind an icon — open it the way a user would.
const searchOpened = await page.evaluate(() => {
  const btn = document.querySelector(
    '.mk-search-toggle, [aria-label*="Search" i], [title*="Search" i], .mk-toolbtn',
  )
  if (!btn) return false
  btn.click()
  return true
})
console.log(`\n   opened an Investments search affordance: ${searchOpened}`)
await page.waitForTimeout(800)
await inventory('Investments (after opening search)')
await focusFirstAndProbe('Investments')

// --- Lucius tab ---
await page.evaluate(() => {
  const slot = [...document.querySelectorAll('.mb-tab')].find((b) =>
    b.textContent.toLowerCase().includes('lucius'),
  )
  slot?.click()
})
await page.waitForTimeout(2000)
await inventory('Lucius')
await focusFirstAndProbe('Lucius')

// What ELSE is pinned near the bottom? If the owner still sees "a nav bar" while
// .mb-tabbar is display:none, it is a different element and we have been fixing the
// wrong one for five rounds.
async function bottomDwellers(label) {
  const els = await page.evaluate(() => {
    const vh = window.innerHeight
    const out = []
    for (const el of document.querySelectorAll('body *')) {
      const cs = getComputedStyle(el)
      if (cs.position !== 'fixed' && cs.position !== 'sticky') continue
      if (cs.display === 'none' || cs.visibility === 'hidden') continue
      const r = el.getBoundingClientRect()
      if (r.height < 8 || r.width < 40) continue
      if (r.bottom < vh - 140 || r.top > vh) continue
      out.push({
        cls: (el.className ? String(el.className) : el.tagName).slice(0, 44),
        pos: cs.position,
        top: Math.round(r.top),
        h: Math.round(r.height),
        z: cs.zIndex,
      })
    }
    return out
  })
  console.log(`\n   bottom-anchored, visible — ${label}: ${els.length}`)
  for (const e of els) {
    console.log(`     ${e.cls}  [${e.pos}] top=${e.top} h=${e.h} z=${e.z}`)
  }
}

await bottomDwellers('Lucius, composer focused, keyboard simulated')
await page.screenshot({ path: '/tmp/kb-lucius-focused.png' })

// ── The command palette: a full-screen FIXED overlay, which the in-flow tab-bar fix does
// not cover. On iOS the layout viewport does not shrink for the keyboard, so a 100dvh
// overlay runs underneath it and its last rows are unreachable.
await page.evaluate(() => {
  document.activeElement instanceof HTMLElement && document.activeElement.blur()
})
await page.evaluate(() => window.__kb.setHeight(844))
await page.waitForTimeout(300)

const openedPalette = await page.evaluate(() => {
  document.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true }),
  )
  return true
})
await page.waitForTimeout(900)
const paletteThere = await page.evaluate(() => !!document.querySelector('.cmdk'))
console.log(`\n   command palette opened: ${openedPalette && paletteThere}`)

if (paletteThere) {
  const measure = () =>
    page.evaluate(() => {
      const overlay = document.querySelector('.modal-overlay:has(.cmdk)')
      const grid = document.querySelector('.cmdk')
      const vv = window.visualViewport
      const r = overlay?.getBoundingClientRect()
      const g = grid?.getBoundingClientRect()
      return {
        visibleBottom: vv ? Math.round(vv.height + vv.offsetTop) : -1,
        layoutBottom: Math.round(window.innerHeight),
        overlayBottom: r ? Math.round(r.bottom) : -1,
        gridTop: g ? Math.round(g.top) : -1,
        cardTop: (() => { const c = document.querySelector('.ui-modal-card'); return c ? Math.round(c.getBoundingClientRect().top) : -1 })(),
        gridBottom: g ? Math.round(g.bottom) : -1,
      }
    })

  await page.evaluate(() => {
    const el = document.querySelector('.cmdk input, .cmdk-input')
    el instanceof HTMLElement && el.focus()
  })
  await page.evaluate(() => window.__kb.setHeight(510))
  await page.waitForTimeout(300)
  const m = await measure()
  console.log(`   keyboard up: visible area ends ${m.visibleBottom}px, layout ${m.layoutBottom}px`)
  console.log(`     backdrop overlay ends ${m.overlayBottom}  (should reach ${m.layoutBottom} — keeps content covered)`)
  console.log(`     card top ${m.cardTop}, grid top ${m.gridTop}`)
  console.log(`     cmdk grid ends ${m.gridBottom}  (should stop at ${m.visibleBottom} — list reachable)`)
  const covers = m.overlayBottom >= m.layoutBottom - 1
  const fits = m.gridBottom <= m.visibleBottom + 1
  console.log(`     ${covers ? 'PASS' : 'FAIL'}  backdrop still covers behind the keyboard`)
  console.log(`     ${fits ? 'PASS' : 'FAIL'}  list ends above the keyboard`)
}

await browser.close()
await inst.teardown?.()
process.exit(0)
