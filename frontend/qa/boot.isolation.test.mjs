import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import http from 'node:http'
import test from 'node:test'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  bootDisposableInstance,
  buildQaChildEnv,
  reserveFreeLoopbackPort,
  resolveQaPython,
  seedDemo,
} from './boot.mjs'

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const worktreeDb = join(repoRoot, 'backend', 'data', 'onw.db')

function digestIfPresent(path) {
  return existsSync(path) ? createHash('sha256').update(readFileSync(path)).digest('hex') : null
}

test('demo seed migrates and writes only to its disposable sqlite database', () => {
  const root = '/tmp/onw-qa-test-root'
  const dataDir = join(root, 'data')
  const cacheDir = join(root, 'cache')
  const logDir = join(root, 'logs')
  const env = buildQaChildEnv({
    sourceEnv: { ONW_DATABASE_URL: 'sqlite:////Users/owner/backend/data/onw.db' },
    dataDir,
    cacheDir,
    logDir,
    demoMode: true,
    port: 8765,
  })
  const calls = []

  seedDemo({
    dataDir,
    env,
    spawnSyncFn: (...args) => {
      calls.push(args)
      return { status: 0 }
    },
  })

  assert.equal(env.DATA_DIR, dataDir)
  assert.equal(env.CACHE_DIR, cacheDir)
  assert.equal(env.LOGS_DIR, logDir)
  assert.equal(env.ONW_DATABASE_URL, `sqlite:///${join(dataDir, 'onw.db')}`)
  assert.equal(env.ONW_BASE_URL, '')
  assert.equal(env.AUTH_API_TOKEN, '')
  assert.equal(calls.length, 1)
  assert.match(calls[0][1][1], /ensure_migrated\(\)/)
  assert.match(calls[0][1][1], /seed_if_empty/)
  assert.equal(calls[0][2].env, env)
})

test('disposable backend receives only its QA runtime contract, never parent secrets', () => {
  const dataDir = '/tmp/onw-qa-test-root/data'
  const env = buildQaChildEnv({
    sourceEnv: {
      PATH: '/safe/bin',
      COINGECKO_DEMO_API_KEY: 'sentinel-demo-key',
      SLACK_BOT_TOKEN: 'sentinel-slack-token',
      CHANNEL_WEBHOOK_URL: 'https://webhook.invalid/sentinel',
      ALPACA_API_SECRET_KEY: 'sentinel-broker-secret',
      TAVILY_API_KEY: 'sentinel-search-key',
      SLACK_ENABLED: 'true',
      CHANNEL_WEBHOOK_ENABLED: 'true',
      ALPACA_ENABLED: 'true',
      SEARCH_PROVIDER: 'tavily',
    },
    dataDir,
    cacheDir: '/tmp/onw-qa-test-root/cache',
    logDir: '/tmp/onw-qa-test-root/logs',
    demoMode: true,
    port: 18765,
  })

  for (const secret of [
    'COINGECKO_DEMO_API_KEY',
    'SLACK_BOT_TOKEN',
    'CHANNEL_WEBHOOK_URL',
    'ALPACA_API_SECRET_KEY',
    'TAVILY_API_KEY',
  ]) {
    assert.equal(env[secret], undefined, `${secret} must not reach disposable QA`)
  }
  assert.equal(env.SLACK_ENABLED, undefined)
  assert.equal(env.SEARCH_PROVIDER, undefined)
  assert.equal(env.CHANNEL_WEBHOOK_ENABLED, undefined)
  assert.equal(env.ALPACA_ENABLED, undefined)
  assert.equal(env.ONW_DISABLE_TELEGRAM, 'true')
  assert.equal(env.ONW_DISABLE_SLACK, 'true')
  assert.equal(env.ONW_DISABLE_DISCORD, 'true')
  assert.equal(env.PATH, '/safe/bin')
  assert.equal(env.PYTHONNOUSERSITE, '1')
  assert.equal(env.DATA_DIR, dataDir)
  assert.equal(env.PORT, '18765')
  assert.equal(env.ONW_QA_MODE, 'true')
  assert.equal(env.PYTHON_DOTENV_DISABLED, 'true')
})

test('QA Python resolver prefers this checkout virtualenv', () => {
  const python = resolveQaPython({
    repoRoot: '/repo/worktree',
    exists: (path) => path === '/repo/worktree/.venv/bin/python3',
    isReady: () => true,
    gitCommonDir: () => '../.git',
  })
  assert.equal(python, '/repo/worktree/.venv/bin/python3')
})

test('QA Python resolver uses only the linked repository checkout virtualenv', () => {
  const python = resolveQaPython({
    repoRoot: '/repo/.worktrees/lane',
    exists: (path) => path === '/repo/.venv/bin/python3',
    isReady: () => true,
    gitCommonDir: () => '../../.git',
  })
  assert.equal(python, '/repo/.venv/bin/python3')
})

test('QA Python resolver skips an unusable worktree virtualenv for the approved shared checkout', () => {
  const python = resolveQaPython({
    repoRoot: '/repo/.worktrees/lane',
    exists: (path) => path === '/repo/.worktrees/lane/.venv/bin/python3' || path === '/repo/.venv/bin/python3',
    gitCommonDir: () => '../../.git',
    isReady: (path) => path === '/repo/.venv/bin/python3',
  })
  assert.equal(python, '/repo/.venv/bin/python3')
})

test('QA Python resolver fails clearly when no approved virtualenv exists', () => {
  assert.throws(
    () => resolveQaPython({ repoRoot: '/repo/worktree', exists: () => false, isReady: () => false, gitCommonDir: () => '../.git' }),
    /QA boot requires Python/,
  )
})

test('the disposable CMD-01 port comes from the non-privileged high range', async () => {
  const port = await reserveFreeLoopbackPort()
  assert.ok(port >= 10_000, `expected high QA port, got ${port}`)
  assert.notEqual(port, 8080)
})

test('a disposable boot rejects a foreign health response on its requested port', async () => {
  const foreign = http.createServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end('{"status":"ok"}')
  })
  await new Promise((resolve) => foreign.listen(0, '127.0.0.1', resolve))
  const address = foreign.address()
  assert.ok(address && typeof address !== 'string')
  try {
    await assert.rejects(
      bootDisposableInstance({ port: address.port, log: () => {} }),
      /backend exited before it became ready|backend did not become healthy/,
    )
  } finally {
    await new Promise((resolve, reject) => foreign.close((error) => error ? reject(error) : resolve()))
  }
})

test('a disposable boot seeds the instruments schema without touching backend/data', async () => {
  const before = digestIfPresent(worktreeDb)
  const boot = await bootDisposableInstance({ port: 8098, log: () => {} })
  try {
    const response = await fetch(`${boot.baseUrl}/api/instruments`)
    assert.equal(response.status, 200)
    const rows = await response.json()
    assert.ok(Array.isArray(rows) && rows.length > 0, 'demo instruments must be queryable')
    assert.ok(existsSync(join(boot.dataDir, 'onw.db')), 'disposable database must exist')
  } finally {
    boot.stop()
  }
  assert.equal(digestIfPresent(worktreeDb), before, 'QA boot must not write backend/data/onw.db')
})
