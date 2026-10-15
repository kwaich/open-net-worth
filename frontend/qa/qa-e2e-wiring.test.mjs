import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const makefile = readFileSync(join(repoRoot, 'Makefile'), 'utf8')
const cmdScript = readFileSync(join(repoRoot, 'frontend', 'qa', 'crypto-market-data-failover.mjs'), 'utf8')
const qaE2eTarget = makefile.slice(
  makefile.indexOf('qa-e2e:'),
  makefile.indexOf('## qa-e2e-crypto-market-data:'),
)
const qaE2eRecipe = qaE2eTarget
  .split('\n')
  .filter((line) => !line.trimStart().startsWith('@#') && !line.trimStart().startsWith('#'))
  .join('\n')

test('qa-e2e runs CMD-01 for all specs and only for the exact CMD-01 token when filtered', () => {
  assert.match(
    makefile,
    /qa-e2e: \$\(if \$\(SPECS\),\$\(if \$\(filter CMD-01,\$\(SPECS\)\),qa-e2e-crypto-market-data,\),qa-e2e-crypto-market-data\) qa-e2e-setup/,
  )
})

test('qa-e2e does not clone QA data while make parses or dry-runs the target', () => {
  assert.doesNotMatch(
    qaE2eRecipe,
    /\$\(shell[^\n]*clone_data_for_qa/,
    'clone_data_for_qa must run inside the failure-safe recipe, never during Makefile parsing',
  )
})

test('make dry-runs CMD-01 for all specs and exact matching filtered specs without cloning data', () => {
  const dryRun = (specs) => {
    const result = spawnSync('make', ['-n', 'qa-e2e', `SPECS=${specs}`], {
      cwd: repoRoot,
      encoding: 'utf8',
    })
    assert.equal(result.status, 0, result.stderr)
    assert.doesNotMatch(result.stdout, /cloned .+ → /)
    return result.stdout
  }
  assert.match(dryRun(''), /npm run e2e:crypto-market-data/)
  assert.match(dryRun('CMD-01'), /npm run e2e:crypto-market-data/)
  assert.doesNotMatch(dryRun('CMD-010'), /npm run e2e:crypto-market-data/)
})

test('CMD-01 watches every asset-detail endpoint and proves metadata isolation through detail and refresh', () => {
  assert.match(cmdScript, /path\.startsWith\('\/api\/intel\/assets\/'\)/)
  assert.match(cmdScript, /\/refresh\?force=true/)
  assert.match(cmdScript, /calls\.metadata\s*>\s*0/)
})

test('CMD-01 inspects the actual Settings key-write response and restored provider order', () => {
  assert.match(cmdScript, /keySaveResponse/)
  assert.match(cmdScript, /Demo key leaked from PUT response/)
  assert.match(cmdScript, /restoredSnapshot/)
  assert.match(cmdScript, /Yahoo-first restoration must attempt Yahoo before CoinGecko/)
  assert.match(cmdScript, /Rollback failure must attempt CoinGecko before Yahoo/)
})

test('CMD-01 proves the required Lucius open and closed viewport matrix', () => {
  assert.match(cmdScript, /for \(const width of \[640, 1024, 1440\]\)/)
  assert.match(cmdScript, /#chat-toggle-btn/)
  assert.match(cmdScript, /assertLuciusDoesNotCoverMarketData/)
  assert.match(cmdScript, /CMD-01-chart-chat-closed-\$\{width\}\.png/)
  assert.match(cmdScript, /CMD-01-chart-chat-open-\$\{width\}\.png/)
  assert.match(cmdScript, /CMD-01-settings-chat-closed-\$\{width\}\.png/)
  assert.match(cmdScript, /CMD-01-settings-chat-open-\$\{width\}\.png/)
})

test('CMD-01 proves 429 fallback, malformed refusal, and identical-request coalescing', () => {
  assert.match(cmdScript, /yahoo_fail_cg_429/)
  assert.match(cmdScript, /malformed_both/)
  assert.match(cmdScript, /delayed_yahoo_fail_cg_live/)
  assert.match(cmdScript, /Promise\.all/)
  assert.match(cmdScript, /yahoo: 1, coingecko: 1/)
  assert.match(cmdScript, /telemetry_24h/)
  assert.match(cmdScript, /coalesced_requests/)
})
