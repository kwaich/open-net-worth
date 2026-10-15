// Computer-use ACCEPTANCE-QA harness (RFC P3 — computer-use-qa, slice S1).
//
//   npm run acceptance-qa -- [--task overview-loads] [--mock]
//        [--max-actions 25] [--max-seconds 120] [--out-dir /tmp/onw-acceptance-qa]
//        [--port 8765] [--model claude-sonnet-4-6]   (or env QA_CU_MODEL)
//
// A vision+action agent that SEES the app (screenshots) and ACTS (clicks/types)
// to exercise a feature's acceptance criteria end-to-end and judge UX. It boots
// its OWN disposable, demo-seeded backend (frontend/qa/boot.mjs) on a dedicated
// port — NEVER the operator's live data — drives a headless Playwright page via
// Anthropic computer use, and writes a structured report + screenshots.
//
// SAFETY: see frontend/qa/boot.mjs (assertSafeBoot) + frontend/qa/README.md. The
// backend is torn down in a finally; a hard wall-clock timeout always kills the
// child and exits. --mock runs a canned action script with NO API call (CI-safe,
// zero token cost) to prove boot + executor + report end-to-end.

import { chromium } from 'playwright'
import { mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { bootDisposableInstance } from './boot.mjs'
import { executeAction, captureScreenshot } from './executor.mjs'

const __dirname = dirname(fileURLToPath(import.meta.url))
const TASKS_DIR = join(__dirname, 'tasks')

const arg = (name, def = null) => {
  const i = process.argv.indexOf(`--${name}`)
  if (i === -1) return def
  const next = process.argv[i + 1]
  return next && !next.startsWith('--') ? next : true
}

const MOCK = arg('mock') === true
const TASK = String(arg('task', 'overview-loads'))
const OUT_DIR = String(arg('out-dir', '/tmp/onw-acceptance-qa'))
const PORT = Number(arg('port', '8765'))
const MAX_ACTIONS = Number(arg('max-actions', '25'))
const MAX_SECONDS = Number(arg('max-seconds', '120'))
// Default to the current Sonnet; override with --model or env QA_CU_MODEL.
const MODEL = String(arg('model', process.env.QA_CU_MODEL || 'claude-sonnet-4-6'))
const VIEWPORT = { width: 1280, height: 800 }

const log = (...a) => console.log(...a)

// ── Hard wall-clock killer (hang-safe) ──────────────────────────────────────
// A bit above --max-seconds so the loop's own soft cap fires first; if anything
// wedges, this always kills the process (and via the exit handler, the child).
const HARD_MS = (MAX_SECONDS + 30) * 1000
let teardown = () => {}
const killer = setTimeout(() => {
  log(`\n[qa] HARD TIMEOUT (${HARD_MS}ms) — killing backend + exiting`)
  try {
    teardown()
  } catch {
    /* ignore */
  }
  process.exit(1)
}, HARD_MS)
killer.unref?.()

// ── Task spec loading ───────────────────────────────────────────────────────
function loadTask(name) {
  const md = join(TASKS_DIR, `${name}.md`)
  const jsonFile = join(TASKS_DIR, `${name}.json`)
  if (existsSync(jsonFile)) {
    return JSON.parse(readFileSync(jsonFile, 'utf-8'))
  }
  if (existsSync(md)) {
    return { name, body: readFileSync(md, 'utf-8') }
  }
  throw new Error(`task spec not found: ${md} (or .json)`)
}

// ── Anthropic computer-use request ──────────────────────────────────────────
function systemPrompt(task) {
  return [
    'You are an acceptance-QA agent driving a web app via COMPUTER USE.',
    'You see a screenshot of the Open Net Worth wealth-manager app and act with the `computer` tool',
    '(screenshot / left_click / type / key / scroll). This is a DISPOSABLE demo instance with',
    'fabricated data — it is safe to click around, but do NOT click destructive buttons like',
    '"Reset all data" / "Clear demo" / "Delete" unless a criterion explicitly requires it.',
    '',
    'Your job: exercise the acceptance criteria below through the real UI, then REPORT.',
    'Work efficiently — you have a hard action budget. Take a screenshot first to orient.',
    '',
    'When done (criteria exercised OR budget nearly spent), STOP calling tools and reply with',
    'ONLY a JSON object (no prose, no markdown fence) of the form:',
    '{"criteria":[{"id":"...","status":"pass|fail|unknown","note":"..."}],',
    ' "bugs":["..."], "usability_verdict":"...", "overall":"pass|fail|partial"}',
    '',
    `TASK: ${task.title || task.name}`,
    task.body || '',
    Array.isArray(task.criteria)
      ? 'CRITERIA:\n' + task.criteria.map((c) => `- [${c.id}] ${c.text}`).join('\n')
      : '',
  ].join('\n')
}

async function callAnthropic({ system, messages, apiKey }) {
  const body = {
    model: MODEL,
    max_tokens: 1024,
    system,
    tools: [
      {
        type: 'computer_20250124',
        name: 'computer',
        display_width_px: VIEWPORT.width,
        display_height_px: VIEWPORT.height,
        display_number: 1,
      },
    ],
    messages,
  }
  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'anthropic-beta': 'computer-use-2025-01-24',
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(60000),
  })
  if (!res.ok) {
    const txt = await res.text()
    throw new Error(`Anthropic API ${res.status}: ${txt.slice(0, 300)}`)
  }
  return res.json()
}

function extractFinalReport(text) {
  // The model is asked to reply with a bare JSON object. Be lenient: pull the
  // first {...} block.
  const m = text.match(/\{[\s\S]*\}/)
  if (!m) return null
  try {
    return JSON.parse(m[0])
  } catch {
    return null
  }
}

// ── The live (API) loop ─────────────────────────────────────────────────────
async function runLiveLoop({ page, task, outDir, apiKey, startedAt }) {
  const system = systemPrompt(task)
  const actionsLog = []
  let report = null
  let shotIndex = 0

  // Seed: an initial screenshot as the first user turn.
  const first = await captureScreenshot(page, { outDir, index: shotIndex++ })
  const messages = [
    {
      role: 'user',
      content: [
        { type: 'text', text: 'Here is the current screen. Begin the acceptance check.' },
        { type: 'image', source: { type: 'base64', media_type: 'image/png', data: first.base64 } },
      ],
    },
  ]

  let actions = 0
  while (actions < MAX_ACTIONS) {
    if ((Date.now() - startedAt) / 1000 > MAX_SECONDS) {
      log('[qa] soft time cap reached — asking for final report skipped; stopping loop')
      break
    }
    const resp = await callAnthropic({ system, messages, apiKey })
    const assistantContent = resp.content || []
    messages.push({ role: 'assistant', content: assistantContent })

    const toolUses = assistantContent.filter((b) => b.type === 'tool_use')
    const textBlocks = assistantContent
      .filter((b) => b.type === 'text')
      .map((b) => b.text)
      .join('\n')

    if (toolUses.length === 0) {
      // Model is done — its text should carry the JSON report.
      report = extractFinalReport(textBlocks)
      log('[qa] model ended turn (no tool use)')
      break
    }

    // Execute each tool_use, return tool_result blocks (screenshots as images).
    const toolResults = []
    for (const tu of toolUses) {
      actions++
      const out = await executeAction(page, tu.input, { outDir, shotIndex, log })
      actionsLog.push({ n: actions, ...tu.input, result: out.line })
      if (out.shot) shotIndex++
      // After any non-screenshot action, capture the resulting screen for the model.
      let shot = out.shot
      if (!shot) {
        await page.waitForTimeout(400)
        shot = await captureScreenshot(page, { outDir, index: shotIndex++ })
      }
      toolResults.push({
        type: 'tool_result',
        tool_use_id: tu.id,
        content: [
          { type: 'image', source: { type: 'base64', media_type: 'image/png', data: shot.base64 } },
        ],
      })
      if (actions >= MAX_ACTIONS) break
    }
    messages.push({ role: 'user', content: toolResults })
  }

  // If we hit a cap without a report, do ONE final no-tools ask for the verdict.
  if (!report) {
    try {
      messages.push({
        role: 'user',
        content: [
          {
            type: 'text',
            text: 'Action budget reached. Reply now with ONLY the JSON report object described in the system prompt.',
          },
        ],
      })
      const resp = await callAnthropic({ system, messages, apiKey })
      const txt = (resp.content || [])
        .filter((b) => b.type === 'text')
        .map((b) => b.text)
        .join('\n')
      report = extractFinalReport(txt)
    } catch (e) {
      log(`[qa] final report request failed: ${e.message}`)
    }
  }

  return { report, actionsLog, actions }
}

// ── The mock loop (no API) ───────────────────────────────────────────────────
// A deterministic canned action script that proves boot + executor + report end
// to end with ZERO API cost: take a screenshot, scroll, screenshot again, and
// synthesize a report from what loaded.
async function runMockLoop({ page, outDir }) {
  const actionsLog = []
  let shotIndex = 0
  const canned = [
    { action: 'screenshot' },
    { action: 'scroll', coordinate: [640, 400], scroll_direction: 'down', scroll_amount: 3 },
    { action: 'screenshot' },
  ]
  for (const input of canned) {
    const out = await executeAction(page, input, { outDir, shotIndex, log })
    actionsLog.push({ ...input, result: out.line })
    if (out.shot) shotIndex++
  }
  // Cheap DOM probe so the mock asserts something real about the loaded app.
  const bodyText = await page.evaluate(() => document.body.innerText || '')
  const looksLoaded = bodyText.length > 50
  const report = {
    criteria: [
      {
        id: 'app-loads',
        status: looksLoaded ? 'pass' : 'fail',
        note: looksLoaded
          ? `app rendered (${bodyText.length} chars of text)`
          : 'page appears blank',
      },
    ],
    bugs: [],
    usability_verdict: 'mock run — no UX judgment (deterministic canned flow, no API call)',
    overall: looksLoaded ? 'pass' : 'fail',
  }
  return { report, actionsLog, actions: canned.length }
}

// ── Report writer ─────────────────────────────────────────────────────────────
function writeReport({ outDir, task, mode, report, actionsLog, actions, elapsedMs }) {
  const full = {
    task: task.name,
    title: task.title || task.name,
    mode,
    model: mode === 'live' ? MODEL : null,
    started_at: new Date(Date.now() - elapsedMs).toISOString(),
    elapsed_seconds: Math.round(elapsedMs / 100) / 10,
    actions_used: actions,
    caps: { max_actions: MAX_ACTIONS, max_seconds: MAX_SECONDS },
    screenshots_dir: outDir,
    report: report || {
      criteria: [],
      bugs: ['harness produced no model report (cap hit or parse failure)'],
      usability_verdict: 'unknown',
      overall: 'partial',
    },
    actions_log: actionsLog,
  }
  const jsonPath = join(outDir, 'report.json')
  writeFileSync(jsonPath, JSON.stringify(full, null, 2))

  // Human-readable summary.
  const r = full.report
  const lines = []
  lines.push(`ACCEPTANCE-QA REPORT — ${full.title}`)
  lines.push(
    `mode=${mode}${full.model ? ` model=${full.model}` : ''}  actions=${actions}/${MAX_ACTIONS}  elapsed=${full.elapsed_seconds}s`,
  )
  lines.push(`overall: ${(r.overall || 'unknown').toUpperCase()}`)
  lines.push('')
  lines.push('criteria:')
  for (const c of r.criteria || []) {
    lines.push(`  [${(c.status || '?').toUpperCase()}] ${c.id}: ${c.note || ''}`)
  }
  lines.push('')
  lines.push(`bugs (${(r.bugs || []).length}):`)
  for (const b of r.bugs || []) lines.push(`  - ${b}`)
  lines.push('')
  lines.push(`usability: ${r.usability_verdict || 'n/a'}`)
  lines.push('')
  lines.push(`screenshots: ${outDir}`)
  lines.push(`report json:  ${jsonPath}`)
  const summary = lines.join('\n')
  writeFileSync(join(outDir, 'report.txt'), summary)
  return { jsonPath, summary, overall: r.overall }
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  const outDir = join(OUT_DIR, `${TASK}-${MOCK ? 'mock' : 'live'}`)
  mkdirSync(outDir, { recursive: true })

  const apiKey = process.env.ANTHROPIC_API_KEY || ''
  if (!MOCK && !apiKey) {
    throw new Error(
      'ANTHROPIC_API_KEY is required for a live run (use --mock for the API-free path)',
    )
  }

  const task = loadTask(TASK)
  log(`[qa] task=${TASK}  mode=${MOCK ? 'mock' : 'live'}  out=${outDir}`)

  const boot = await bootDisposableInstance({ port: PORT, log })
  teardown = boot.stop

  const browser = await chromium.launch()
  const startedAt = Date.now()
  let result
  try {
    const ctx = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1 })
    // Pin the target tab + close the chat so the agent lands where the task expects.
    await ctx.addInitScript(
      ([tab]) => {
        if (tab) localStorage.setItem('wealthTab', tab)
        localStorage.setItem('chat-open', 'false')
      },
      [task.tab || 'overview'],
    )
    const page = await ctx.newPage()
    await page.goto(`${boot.baseUrl}/`, { waitUntil: 'domcontentloaded', timeout: 20000 })
    await page.waitForTimeout(1500)

    result = MOCK
      ? await runMockLoop({ page, outDir })
      : await runLiveLoop({ page, task, outDir, apiKey, startedAt })
  } finally {
    await browser.close()
    boot.stop()
    teardown = () => {}
  }

  const elapsedMs = Date.now() - startedAt
  const { summary, overall } = writeReport({
    outDir,
    task,
    mode: MOCK ? 'mock' : 'live',
    report: result.report,
    actionsLog: result.actionsLog,
    actions: result.actions,
    elapsedMs,
  })

  log('\n' + '='.repeat(72))
  log(summary)
  log('='.repeat(72))

  clearTimeout(killer)
  // Exit code: 0 unless the run clearly failed. "partial"/"unknown" → 0 (the
  // harness ran; the verdict is informational, read the report).
  process.exit(overall === 'fail' ? 2 : 0)
}

main().catch((e) => {
  log(`\n[qa] FATAL: ${e.message}`)
  try {
    teardown()
  } catch {
    /* ignore */
  }
  clearTimeout(killer)
  process.exit(1)
})
