#!/usr/bin/env node
/**
 * check-entity-rows.mjs — WARN-ONLY entity-row coverage lint (Phase 1).
 *
 * Flags two classes of issues:
 *
 *   (A) Entity emitters NOT using EntityRow:
 *       Finds raw buildCtxFields( / entityRefProps( / data-entity-id= calls
 *       outside the allowlisted files.  In Phase 1 this is WARN-ONLY; the error
 *       flip happens after the 9 existing emitters migrate in step 1e.
 *
 *   (B) ONW-domain imports in the would-be-core module set (§14.8 R-EX1):
 *       The core modules must be free of @/api, @/state, @/hooks imports
 *       (except pure type-only screen snapshot imports) so Phase-4 extraction
 *       doesn't require a rewrite.
 *
 * Usage:
 *   node scripts/check-entity-rows.mjs         # warn-only (always exit 0)
 *   CHECK_ENTITY_STRICT=1 node scripts/check-entity-rows.mjs  # exit 1 on findings
 *
 * The strict flag is intentionally not wired into `make build` yet — that
 * happens in step 1e when the 9 emitters are migrated.
 */

import { readFileSync, readdirSync, statSync } from 'fs'
import { join, relative } from 'path'
import { fileURLToPath } from 'url'

const ROOT = fileURLToPath(new URL('../../', import.meta.url))
const SRC = join(ROOT, 'frontend/src')
const STRICT = process.env['CHECK_ENTITY_STRICT'] === '1'

// ── (A) Allowlist — files that may legitimately call buildCtxFields /
//      entityRefProps / emit data-entity-id without using EntityRow.
//
// Categories:
//   [core]     — the primitives themselves; the raw helpers live here
//   [singleton]— singleton hero emitters (SummaryBar, net-worth masthead)
//                that are not list emitters and are explicitly allowlisted
//
// The 9 step-1e emitters have migrated to <EntityRow> and are no longer
// allowlisted — the strict check is now enforced for them (step 1e complete).
// ─────────────────────────────────────────────────────────────────────────────
const EMITTER_ALLOWLIST = new Set([
  // [core] The attribute helpers — source of truth, must call themselves.
  'utils/mentionable.ts',
  'lucius/ctxFields.ts',
  'lucius/EntityRow.tsx',
  'lucius/EntityList.tsx',

  // [singleton] SummaryBar uses entityRefProps for a single stat hero — not a list.
  'components/ui/SummaryBar.tsx',

  // [singleton] AssetDetailView tags its data-ctx-region page-root <div> with the
  // subject instrument via entityRefProps — a screen-region wrapper, not a list row,
  // so it cannot be an <EntityRow>. Its actual peer list IS migrated to <EntityRow>.
  'components/intel/AssetDetailView.tsx',

  // [custom-row] The Budget CategoryRow is a bespoke multi-cell envelope row (assign /
  // activity / available cells, inline edit, drag-reorder, money-moves popover) — it is
  // not a generic mention list, so it cannot be an <EntityRow>. Its entityRefProps stamp
  // is what makes the row @-mentionable and drag-to-Lucius capable (owner-requested).
  'components/tabs/plan/budget.tsx',
  // [custom-row] 50/30/20's envelope row and its phone card — the same bespoke multi-cell row as
  // Zero-Based's (cap / activity / left, inline edit, drag-reorder), stamped the same way.
  'components/tabs/plan/budgetLens/proportional/PropRows.tsx',
  'components/tabs/plan/budgetLens/proportional/PropCards.tsx',
])

// ── (B) Core module set — must be free of ONW-domain imports (§14.8 R-EX1).
//
// These files are the "would-be-@lucius/core" set.  Any import of @/api,
// @/state, @/hooks (except pure type imports from screenContract types) is a
// layering violation that would block extraction.
//
// NOT in this set (intentionally host-specific):
//   lucius/onwDataSource.ts  — the ONW DataSourceAdapter (imports authFetch from @/api)
//   lucius/adapters.ts       — exports wireDataSource (pure types/seam, no ONW imports,
//                               but hosts may supply their own implementation file)
// ─────────────────────────────────────────────────────────────────────────────
const CORE_MODULE_SET = new Set([
  'lucius/screenContract.ts',
  'lucius/objectFields.ts',
  'lucius/ctxFields.ts',
  'lucius/mentionColors.ts',
  'lucius/matchEntity.ts',
  'lucius/mentionRow.ts',
  'lucius/harvest.ts',
  'lucius/mergeRows.ts',
  'lucius/EntityRow.tsx',
  'lucius/EntityList.tsx',
  'lucius/security/fence.ts',
  'lucius/entityRegistry.ts',
])

// ONW-domain import patterns banned in core modules.
// Exceptions: pure `import type` from screenContract-adjacent snapshot types
// are permitted — the ban is on runtime/value imports from api/state/hooks.
const DOMAIN_IMPORT_RE = /from ['"](@\/api|@\/state|@\/hooks|\.\.\/api\/|\.\.\/state\/|\.\.\/hooks\/)/

// ── helpers ───────────────────────────────────────────────────────────────────

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    try {
      const s = statSync(full)
      if (s.isDirectory()) walk(full, out)
      else if (/\.(tsx?|mts)$/.test(entry)) out.push(full)
    } catch {
      // skip unreadable entries
    }
  }
  return out
}

// Pattern for (A): raw attribute-stamping helpers outside EntityRow.
// We match:
//   buildCtxFields(  — call to the write helper
//   entityRefProps(  — call to the ref-props helper
//   data-entity-id=  — JSX prop emission (not inside a string/selector)
// We deliberately exclude `data-entity-id` preceded by [ (CSS selector read)
// and by data- in a comment line.
const RAW_EMITTER_RE =
  /buildCtxFields\s*\(|entityRefProps\s*\(|(?<!\[)data-entity-id=(?!["'`]\s*\])/

const findings = []

// ── (A) Check for raw emitters outside the allowlist ─────────────────────────

for (const abs of walk(SRC)) {
  const rel = relative(SRC, abs).replace(/\\/g, '/')
  if (EMITTER_ALLOWLIST.has(rel)) continue
  if (rel.endsWith('.test.tsx') || rel.endsWith('.test.ts')) continue

  let src
  try { src = readFileSync(abs, 'utf8') } catch { continue }

  const lines = src.split('\n')
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? ''
    // Skip comment lines (doc comments, line comments) — they mention the API
    // by name but don't emit attributes.
    const trimmed = line.trimStart()
    if (trimmed.startsWith('//') || trimmed.startsWith('*')) continue
    const m = RAW_EMITTER_RE.exec(line)
    if (m) {
      findings.push({
        file: rel,
        line: i + 1,
        col: m.index + 1,
        msg: `raw buildCtxFields/entityRefProps/data-entity-id outside EntityRow — use <EntityRow> instead`,
      })
    }
  }
}

// ── (B) Check core modules for ONW-domain imports ────────────────────────────

for (const rel of CORE_MODULE_SET) {
  const abs = join(SRC, rel)
  let src
  try { src = readFileSync(abs, 'utf8') } catch { continue }

  const lines = src.split('\n')
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? ''
    // Pure `import type { … }` from api/state/hooks is permitted — the ban is on
    // runtime/value imports (see the §14.8 R-EX1 note above).
    if (/^\s*import\s+type\b/.test(line)) continue
    if (DOMAIN_IMPORT_RE.test(line)) {
      findings.push({
        file: rel,
        line: i + 1,
        col: 1,
        msg: `ONW-domain import in core module (§14.8 R-EX1) — core modules must not import @/api, @/state, @/hooks`,
      })
    }
  }
}

// ── Report ────────────────────────────────────────────────────────────────────

if (findings.length === 0) {
  console.log('check-entity-rows: OK (0 findings)')
  process.exit(0)
}

const prefix = STRICT ? 'ERROR' : 'WARN'
for (const f of findings) {
  console.log(`${prefix}  ${f.file}:${f.line}:${f.col}  ${f.msg}`)
}
console.log(`\ncheck-entity-rows: ${findings.length} finding(s) [${STRICT ? 'STRICT — exit 1' : 'warn-only'}]`)

if (STRICT) {
  process.exit(1)
}
// warn-only — always exit 0 in Phase 1
process.exit(0)
