import { chromium } from '../../frontend/node_modules/playwright/index.mjs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { mkdir } from 'node:fs/promises'

const here = path.dirname(fileURLToPath(import.meta.url))
const fixture = path.join(here, 'fixtures', 'statement-import-handoff.ofx')
const out = path.join(here, 'artifacts', 'statement-import-parity')
const appUrl = process.env.APP_URL || 'http://localhost:8092'
const backendUrl = process.env.BACKEND_URL || 'http://localhost:8091'
await mkdir(out, { recursive: true })

function check(condition, message) {
  if (!condition) throw new Error(message)
}

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })

try {
  // Disposable setup through the real ledger write choke. The imported Grocer row must resolve
  // to this entry so the browser gate can prove collapsed ledger evidence + expanded comparison.
  const accountsResponse = await page.request.get(`${backendUrl}/api/accounts`)
  check(accountsResponse.ok(), `GET /api/accounts failed (${accountsResponse.status()})`)
  const accountsPayload = await accountsResponse.json()
  const accounts = Array.isArray(accountsPayload) ? accountsPayload : accountsPayload.accounts
  const account = accounts.find((candidate) => !candidate.archived && candidate.currency === 'SGD')
  check(account?.id && account?.currency && account?.name, 'No active account available in QA data')

  // IDEMPOTENCE: this gate commits its fixture import, which stamps the fixture's own external
  // refs onto the ledger. A second run against the SAME data dir would therefore see the Grocer
  // row L0-deduped (correct product behaviour — external-ref protection) and could never exercise
  // the matched-row anatomy. So clear any prior run's artefacts first, and the gate becomes
  // repeatable instead of once-per-clone.
  const priorResponse = await page.request.get(`${backendUrl}/api/transactions?limit=2000`)
  if (priorResponse.ok()) {
    const priorPayload = await priorResponse.json()
    const priorRows = Array.isArray(priorPayload) ? priorPayload : (priorPayload.transactions || [])
    const stale = priorRows.filter((row) =>
      (row.external_refs || []).some((ref) => String(ref?.ref || '').includes('qa-import-'))
      || ['Grocer', 'QA Alternative'].includes(row.description))
    for (const row of stale) {
      await page.request.delete(`${backendUrl}/api/transactions/${row.id}`)
    }
    if (stale.length) console.log(`  cleared ${stale.length} artefact(s) from a previous run`)
  }

  const seedResponse = await page.request.post(`${backendUrl}/api/transactions`, {
    data: {
      date: '2026-06-01',
      kind: 'expense',
      description: 'Grocer',
      from_account_id: account.id,
      from_amount: 42.15,
      from_currency: account.currency,
      cleared: false,
      source: 'manual',
    },
  })
  check(seedResponse.ok(), `Could not seed deterministic Grocer match (${seedResponse.status()})`)
  const seededGrocer = await seedResponse.json()
  const alternateResponse = await page.request.post(`${backendUrl}/api/transactions`, {
    data: {
      date: '2026-06-01',
      kind: 'expense',
      description: 'QA Alternative',
      from_account_id: account.id,
      from_amount: 41.90,
      from_currency: account.currency,
      cleared: false,
      source: 'manual',
    },
  })
  check(alternateResponse.ok(), `Could not seed deterministic alternate match (${alternateResponse.status()})`)
  const seededAlternative = await alternateResponse.json()
  check(
    seededGrocer?.id && seededAlternative?.id && seededGrocer.id !== seededAlternative.id,
    `QA ledger seeds did not create distinct rows: ${JSON.stringify({ seededGrocer, seededAlternative })}`,
  )

  await page.goto(appUrl, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: 'Show amounts' }).click().catch(() => {})
  await page.getByRole('button', { name: 'Accounts' }).click()
  await page.getByRole('button', { name: 'Import', exact: true }).click()

  const dialog = page.getByRole('dialog', { name: 'Import a statement' })
  await dialog.waitFor()
  const uploadButton = dialog.getByRole('button', { name: 'Choose a statement file or drop it here' })
  check(await uploadButton.isVisible(), 'Upload drop zone is not a named semantic button')
  const uploadInput = dialog.locator('input[type="file"][aria-label="Statement file"]')
  check(await uploadInput.count() === 1, 'Backing statement file input is not labelled')
  await uploadInput.setInputFiles(fixture)

  // The Choose step is the handoff's own DropStep: the account is a CHIP, picked from a
  // popover, not a DS dropdown. With nothing chosen the add-chip reads "Select account".
  const accountPicker = dialog.getByRole('button', { name: /Select account|\+ Add account/ })
  await accountPicker.click()
  await dialog.getByRole('option').filter({ hasText: account.name }).click()

  // OFX enters the one production preview pipeline through its deterministic parser (zero
  // model calls) and declares a period, so this gate also exercises the real ledger check.
  const previewResponse = page.waitForResponse((response) =>
    response.url().includes('/api/import/preview') && response.request().method() === 'POST')
  await dialog.getByRole('button', { name: 'Read the statement', exact: true }).click()
  const previewPayload = await (await previewResponse).json()

  const statementSection = dialog.getByRole('button', { name: /The statement — top to bottom/ })
  await statementSection.waitFor({ timeout: 90_000 })
  const crumbs = await dialog.locator('.imp3-crumb').allTextContents()
  check(JSON.stringify(crumbs) === JSON.stringify(['Choose', 'Read', 'Decide']), 'Choose / Read / Decide hierarchy drifted')

  const grocerRow = dialog.locator('.imp3-arow').filter({ hasText: 'Grocer' }).first()
  await grocerRow.waitFor()
  check(await grocerRow.locator('.imp3-arow-match').isVisible(),
    // If this fails with the row classed `dup`, the QA data dir already contains a prior commit of
    // this fixture (its external_refs make the row L0-deduped) — re-clone the data dir.
    `Matched ledger evidence is not visible before expansion (row class=${await grocerRow.getAttribute('class')})`)
  const rowTrigger = grocerRow.locator('.imp3-arow-line')
  await rowTrigger.focus()
  await rowTrigger.press('Enter')
  check(await rowTrigger.getAttribute('aria-expanded') === 'true', 'Keyboard disclosure did not expand the matched row')

  // The design's own labels: the two compare column heads, then the After panel naming itself.
  const comparisonLabels = await grocerRow.locator('.imp3-cmp-h, .imp3-after-l').allTextContents()
  check(
    JSON.stringify(comparisonLabels.map((label) => label.replace(/ · .*$/, ''))) ===
      JSON.stringify(['On the statement', 'In your ledger', 'After import']),
    `Comparison hierarchy drifted: ${comparisonLabels.join(' → ')}`,
  )
  check(await grocerRow.locator('.imp-pair-body').count() === 0, 'Rejected three-peer comparison structure is still present')
  const decisionGroup = grocerRow.getByRole('radiogroup', { name: /What happens to Grocer/ })
  for (const label of ['Link & update', 'Keep both', 'Leave out']) {
    check(await decisionGroup.getByRole('radio', { name: label }).isVisible(), `Missing ${label} decision`)
  }
  check(await grocerRow.locator('.imp3-after').isVisible(), 'After import projection is missing')
  const detailOrder = await grocerRow.evaluate((row) => {
    const comparison = row.querySelector('.imp3-cmp')
    const decision = row.querySelector('.imp3-actrow')
    const after = row.querySelector('.imp3-after')
    if (!comparison || !decision || !after) return null
    const follows = (before, afterNode) => Boolean(before.compareDocumentPosition(afterNode) & Node.DOCUMENT_POSITION_FOLLOWING)
    return {
      comparisonBeforeDecision: follows(comparison, decision),
      decisionBeforeAfter: follows(decision, after),
    }
  })
  check(
    detailOrder?.comparisonBeforeDecision && detailOrder?.decisionBeforeAfter,
    `Expanded detail order drifted: ${JSON.stringify(detailOrder)}`,
  )
  // The verdict is ONE recessed panel whose every line pairs a round status badge with a bold
  // headline and a dim breakdown. The tie-out math waterfall is GONE (owner 2026-07-31 — its
  // "adds up" tick was tautological, see ledger_diff.py) and must not come back.
  const verdict = dialog.locator('.imp3-verdict')
  check(await verdict.count() === 1, 'The verdict is not one panel')
  const verdictLines = await verdict.locator('.imp3-vline').count()
  check(verdictLines >= 1, 'The verdict has no badge+headline lines')
  check(await verdict.locator('.imp3-vic').count() === verdictLines, 'A verdict line is missing its status badge')
  check(await verdict.locator('.imp-math, .imp-mathfold').count() === 0, 'The tie-out math waterfall is back')
  // The facts line above it: account (own colour dot) · period · reconciled · cost on the right.
  const facts = dialog.locator('.imp3-facts')
  check(await facts.count() === 1, 'The statement facts line is missing')
  check(await facts.locator('i').first().isVisible(), 'The facts line has no account colour dot')
  // The rows section leads with a count badge; the Needs-you queue is a plain header.
  check(await dialog.locator('.imp3-sec-badge').first().isVisible(), 'The section header has no count badge')

  await decisionGroup.getByRole('radio', { name: 'Keep both' }).click()
  check((await grocerRow.locator('.imp3-dchip').first().textContent())?.trim() === 'Keep both', 'Scan verdict did not react to Keep both')
  await decisionGroup.getByRole('radio', { name: 'Link & update' }).click()
  check(await decisionGroup.getByRole('radio', { name: 'Link & update' }).getAttribute('aria-checked') === 'true', 'Link choice is not checked')

  await decisionGroup.getByRole('radio', { name: 'Link & update' }).focus()
  await page.keyboard.press('Shift+Tab')
  await page.keyboard.press('Tab')
  const focusState = await page.evaluate(() => {
    const element = document.activeElement
    if (!(element instanceof HTMLElement)) return null
    const style = getComputedStyle(element)
    return {
      role: element.getAttribute('role'),
      name: element.textContent?.trim(),
      outlineStyle: style.outlineStyle,
      outlineWidth: style.outlineWidth,
      boxShadow: style.boxShadow,
    }
  })
  check(
    focusState?.role === 'radio'
      && ((focusState.outlineStyle !== 'none' && focusState.outlineWidth !== '0px') || focusState.boxShadow !== 'none'),
    `Decision focus indicator is not visible: ${JSON.stringify(focusState)}`,
  )

  // Exercise the actual pointer path that regressed when the match popover sat below the
  // modal's scroll surface. Restore the engine choice so the commit still proves its own pick.
  const changeMatch = grocerRow.getByRole('button', { name: 'Change match' })
  check(
    await changeMatch.isVisible(),
    `Change match is missing despite an uncovered ledger alternative: ${JSON.stringify({
      grocerId: seededGrocer.id,
      alternativeId: seededAlternative.id,
      payloadKeys: Object.keys(previewPayload || {}),
      ledgerCheck: previewPayload?.ledger_check,
      ledgerRows: (previewPayload?.ledgerCheck?.rows || previewPayload?.ledger_check?.rows)?.map((row) => ({ id: row.id, note: row.note, amount: row.amount })),
      boundary: (previewPayload?.ledgerCheck?.boundary || previewPayload?.ledger_check?.boundary)?.map((row) => ({ id: row.id, note: row.note, amount: row.amount })),
    })}`,
  )
  await changeMatch.click()
  // ONE picker popover, one option list (the design's single LinkPicker).
  check(await dialog.locator('.imp3-lpick-pop').count() === 1,
    `Expected exactly one open picker, saw ${await dialog.locator('.imp3-lpick-pop').count()}`)
  const alternative = dialog.locator('.imp3-lpick-pop').getByRole('option').filter({ hasText: 'QA Alternative' })
  // >= 1: each run of this gate seeds its own disposable alternate, so a re-run against the
  // same throwaway data dir legitimately accumulates them.
  check(await alternative.count() >= 1, 'The picker does not list the uncovered alternate')
  await alternative.first().click()
  check(await grocerRow.getByRole('button', { name: "Back to Lucius's pick" }).isVisible(), 'Pointer Change match did not apply')
  await grocerRow.getByRole('button', { name: "Back to Lucius's pick" }).click()

  // Link one real unmatched row to the uncovered alternate. That promotes it through the
  // production adapter into Needs you; prove receipt/Undo, then unlink before commit.
  const needsChip = dialog.getByRole('button', { name: /Needs you/ })
  if (!await needsChip.isVisible()) {
    const plainRows = dialog.locator('.imp3-arow.plain')
    for (let index = 0; index < await plainRows.count(); index += 1) {
      const row = plainRows.nth(index)
      if (await row.locator('.imp3-arow-line').getAttribute('aria-expanded') !== 'true') {
        await row.locator('.imp3-arow-line').click()
      }
    }
    // ONE picker vocabulary across every row: the design's `⇄ Link to existing`.
    const manualLinkButton = dialog.getByRole('button', { name: /Link to existing/ }).first()
    check(await manualLinkButton.isVisible(), 'No unmatched statement row exposed the production manual-link control')
    await manualLinkButton.click()
    await dialog.locator('.imp3-lpick-pop').getByRole('option').filter({ hasText: 'QA Alternative' }).first().click()
  }
  check(await needsChip.isVisible(), 'Manual linking did not promote the row into Needs you')
  await needsChip.click()
  const queueCard = dialog.locator('.imp3-qcard').first()
  // The card's title is the design's one-line "payee · date · amount".
  const queueTitle = (await queueCard.locator('.imp3-qtitle').textContent())?.split('·')[0]?.trim()
  await queueCard.waitFor()
  await queueCard.getByRole('button', { name: /keep both/i }).click()
  check(await queueCard.getByRole('button', { name: 'Undo' }).isVisible(), 'Needs-you card did not retain its receipt and Undo')
  await page.waitForTimeout(200)
  check(
    await queueCard.getByRole('button', { name: /keep both/i }).getAttribute('aria-pressed') === 'true'
      && await queueCard.getByRole('button', { name: /Same one/ }).getAttribute('aria-pressed') === 'false',
    'Needs-you receipt and selected decision disagree',
  )
  await page.screenshot({ path: path.join(out, 'production-needs-you.png'), fullPage: false })
  await queueCard.getByRole('button', { name: 'Undo' }).click()
  await dialog.getByRole('button', { name: /All \d+/ }).click()
  if (queueTitle) {
    const linkedRow = dialog.locator('.imp3-arow').filter({ hasText: queueTitle }).first()
    if (await linkedRow.getByRole('button', { name: 'Unlink' }).count()) {
      await linkedRow.getByRole('button', { name: 'Unlink' }).click()
    }
  }

  // Leave-out evidence is captured from the same real matched row and then restored.
  await decisionGroup.getByRole('radio', { name: 'Leave out' }).click()
  check((await grocerRow.locator('.imp3-dchip').first().textContent())?.trim() === 'Leave out', 'Left-out decision is not explicit')
  await page.waitForTimeout(200)
  check(
    await decisionGroup.getByRole('radio', { name: 'Leave out' }).getAttribute('aria-checked') === 'true'
      && await decisionGroup.getByRole('radio', { name: 'Link & update' }).getAttribute('aria-checked') === 'false',
    'Left-out result and selected decision disagree',
  )
  await page.screenshot({ path: path.join(out, 'production-left-out.png'), fullPage: false })
  await decisionGroup.getByRole('radio', { name: 'Link & update' }).click()
  await page.waitForTimeout(200)

  const states = [
    { width: 1440, height: 1000, label: '1440' },
    { width: 1024, height: 900, label: '1024' },
    { width: 390, height: 844, label: 'mobile' },
  ]

  for (const theme of ['dark', 'light']) {
    await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme)
    for (const state of states) {
      await page.setViewportSize({ width: state.width, height: state.height })
      const overflow = await page.evaluate(() => ({
        page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        modal: (() => {
          const element = document.querySelector('[role=dialog]')
          return element ? element.scrollWidth - element.clientWidth : 0
        })(),
      }))
      check(overflow.page <= 1 && overflow.modal <= 1, `${state.label}px ${theme} has horizontal overflow`)
      if (state.label === 'mobile') {
        // The handoff is explicitly desktop-only ("no mobile/narrow-width pass on the new
        // fixed-width columns yet"), so there is no canvas geometry to pin here. What must hold
        // is that the sheet stays INSIDE the viewport with a visible inset on both edges and
        // never taller than the viewport — the rest of the mobile contract is the stacking
        // check below plus the no-overflow check above.
        const mobileBox = await dialog.evaluate((element) => {
          const rect = element.getBoundingClientRect()
          return { left: rect.left, right: rect.right, width: rect.width, height: rect.height,
            vw: window.innerWidth, vh: window.innerHeight }
        })
        check(
          mobileBox.left >= 8
            && mobileBox.right <= mobileBox.vw - 8
            && mobileBox.width >= mobileBox.vw * 0.85
            && mobileBox.height <= mobileBox.vh,
          `Mobile sheet is not inset inside the viewport: ${JSON.stringify(mobileBox)}`,
        )
        const mobileRow = dialog.locator('.imp3-arow').filter({ hasText: 'Taxi' }).first()
        // The narrow contract (statementImport.css): the AMOUNT joins the payee on the first
        // line — the two facts you scan together — and the decision + category rails drop to a
        // second line at full width. Nothing is hidden and nothing overlaps.
        const mobileGeometry = await mobileRow.evaluate((row) => {
          const box = (selector) => {
            const element = row.querySelector(selector)
            return element ? element.getBoundingClientRect() : null
          }
          const name = box('.imp3-arow-main')
          const status = box('.imp3-arow-status')
          const category = box('.imp3-arow-catcol')
          const amount = box('.imp3-arow-amtwrap')
          if (!name || !status || !category || !amount) return null
          const overlaps = (a, b) =>
            Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
            Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1
          return {
            amountOnFirstLine: amount.top < status.top + 1 && amount.top < category.top + 1,
            amountBesideName: Math.min(amount.bottom, name.bottom) - Math.max(amount.top, name.top) > 1,
            statusOverlapsCategory: overlaps(status, category),
            statusOverlapsAmount: overlaps(status, amount),
            categoryOverlapsAmount: overlaps(category, amount),
            allVisible: [name, status, category, amount].every((rect) => rect.width > 0 && rect.height > 0),
          }
        })
        check(
          mobileGeometry
            && mobileGeometry.amountOnFirstLine
            && mobileGeometry.amountBesideName
            && !mobileGeometry.statusOverlapsCategory
            && !mobileGeometry.statusOverlapsAmount
            && !mobileGeometry.categoryOverlapsAmount
            && mobileGeometry.allVisible,
          `Mobile row reflow drifted: ${JSON.stringify(mobileGeometry)}`,
        )
      }

      // Lucius WITH the workspace modal open. The dock is normally the top tier (--z-toast+10)
      // so it can float over page content (ADR 0028) — but this modal fills the viewport and
      // puts its primary action at the bottom-right, exactly where the expanded dock sits, so
      // while a workspace modal is open the dock steps BEHIND it (statementImport.css). It is
      // not interactive then anyway: the modal traps focus. Both matrix cells therefore capture
      // the same guarantee — the dock never occludes a control the user must reach.
      const dock = page.locator('.lx-dock')
      const dockState = await dock.count()
        ? await dock.evaluate((element) => element.classList.contains('lx-collapsed') ? 'collapsed' : 'expanded')
        : 'absent'
      await page.screenshot({
        path: path.join(out, `production-${state.label}-${theme}-lucius-closed.png`),
        fullPage: false,
      })

      if (state.width > 640 && await dock.count()) {
        // A PAINT test, not a bounding-box test: what matters is whether anything from the dock
        // paints on top of a control the user must be able to hit. Same technique the repo's own
        // visual-qa gate uses (frontend/scripts/visual-qa.mjs).
        const dockOverlap = await page.evaluate(() => {
          const targets = [
            ['decision group', document.querySelector('.imp-pair-row [role="radiogroup"]')],
            ['After import', document.querySelector('.imp-pair-row .imp3-after')],
            ['commit CTA', document.querySelector('[role="dialog"] .imp3-commit')],
          ]
          return targets.map(([name, element]) => {
            if (!(element instanceof Element)) return { name, overlap: true }
            const rect = element.getBoundingClientRect()
            const points = [
              [rect.left + rect.width * 0.5, rect.top + rect.height * 0.5],
              [rect.left + 4, rect.top + 4],
              [rect.right - 4, rect.bottom - 4],
            ]
            const covered = points.some(([x, y]) => {
              const hit = document.elementFromPoint(x, y)
              return Boolean(hit) && !element.contains(hit) && Boolean(hit.closest('.lx-dock'))
            })
            return { name, overlap: covered ? 1 : 0 }
          })
        })
        check(
          dockOverlap.every(({ overlap }) => overlap <= 0),
          `Lucius dock paints over review controls (dock ${dockState}): ${JSON.stringify(dockOverlap)}`,
        )
        // The commit CTA must be genuinely clickable, not merely un-occluded at three points.
        await page.locator('[role="dialog"] .imp3-commit').hover({ trial: true })
        await page.screenshot({
          path: path.join(out, `production-${state.label}-${theme}-lucius-open.png`),
          fullPage: false,
        })
      }
    }
  }

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'))
  const dock = page.locator('.lx-dock')
  if (await dock.count() && !await dock.evaluate((element) => element.classList.contains('lx-collapsed'))) {
    await page.locator('button.lx-dock-collapse[aria-label="Collapse"]').click()
    await page.waitForFunction(() => document.querySelector('.lx-dock')?.classList.contains('lx-collapsed'))
    await page.waitForTimeout(400)
  }
  await page.screenshot({ path: path.join(out, 'production-review.png'), fullPage: false })

  await dialog.getByRole('button', { name: /Confirm & book/ }).click()
  await dialog.getByText('Booked.', { exact: true }).waitFor({ timeout: 30_000 })
  const markReviewed = dialog.getByRole('button', { name: /Mark all \d+ as reviewed/ })
  if (await markReviewed.count()) {
    await markReviewed.click()
    await dialog.getByText(/marked as reviewed/).waitFor()
  }
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(out, 'production-completion.png'), fullPage: false })

  // On the real mobile shell Lucius is a full destination, not an overlay over Accounts.
  // Capture that honest open state only after the import evidence is complete.
  await dialog.getByRole('button', { name: 'Close' }).click()
  await dialog.waitFor({ state: 'detached' })
  await page.setViewportSize({ width: 390, height: 844 })
  for (const theme of ['dark', 'light']) {
    await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme)
    const luciusTab = page.getByRole('button', { name: 'Lucius' })
    await luciusTab.click()
    await page.locator('.app-layout--lucius .lx-side').waitFor()
    check(await luciusTab.getAttribute('aria-current') === 'page', `Mobile Lucius ${theme} state is not selected`)
    await page.waitForTimeout(400)
    await page.screenshot({
      path: path.join(out, `production-mobile-${theme}-lucius-open.png`),
      fullPage: false,
    })
  }

  console.log('PASS: statement import handoff deterministic E2E')
} finally {
  await browser.close()
}
