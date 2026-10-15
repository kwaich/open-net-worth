// Disposable-instance boot for the computer-use acceptance-QA harness.
//
// Spawns a THROWAWAY Open Net Worth backend whose DATA_DIR + CACHE_DIR live in a
// fresh mktemp dir, demo-seeded, on a DEDICATED port (default 8765, NEVER 8080),
// serving the built frontend/dist. The harness drives THIS instance — never the
// operator's live data — and tears it down in a finally.
//
// SAFETY (a vision agent has a mouse — it can click "Reset all data"):
//   - assertSafeBoot() refuses to run unless the port != 8080 and DATA_DIR is a
//     fresh tmp dir that does NOT contain the live "~/.open-net-worth" segment.
//   - the child is killed on exit (the caller's finally); no server is left up.
//   - loopback-only host (127.0.0.1); auth disabled (throwaway box).
//   - a constructed child environment blocks operator credentials, integrations,
//     and `.env` loading before the backend imports any application module.
//
// This module is import-only (the app never imports it). It uses the repo venv
// python to seed + boot, mirroring backend/scripts/seed_examples.py + main.py.

import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, existsSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// Derive the repo root from THIS file's location (frontend/qa/boot.mjs → ../..),
// never a hard-coded absolute path — so the harness works in CI, git worktrees,
// and any clone, not just one machine.
const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const DIST_DIR = join(REPO_ROOT, 'frontend', 'dist')

// Hard refusal: the live data lives under this path segment. The disposable
// instance must NEVER point here.
const LIVE_DATA_SEGMENT = '/.open-net-worth/'

/** Reserve then release a non-privileged loopback port for a disposable child. */
export async function reserveFreeLoopbackPort({ minPort = 10_000, attempts = 20 } = {}) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const reservation = createServer()
    await new Promise((resolve, reject) => {
      reservation.once('error', reject)
      reservation.listen(0, '127.0.0.1', resolve)
    })
    const address = reservation.address()
    await new Promise((resolve, reject) => reservation.close((error) => error ? reject(error) : resolve()))
    if (address && typeof address !== 'string' && address.port >= minPort) return address.port
  }
  throw new Error(`could not reserve a non-privileged loopback port after ${attempts} attempts`)
}

/** Resolve QA Python from this checkout or its linked repository checkout only. */
export function resolveQaPython({
  repoRoot = REPO_ROOT,
  exists = existsSync,
  isReady = (python) => {
    const result = spawnSync(python, ['-c', 'import fastapi, dotenv'], {
      cwd: repoRoot,
      stdio: 'ignore',
    })
    return result.status === 0
  },
  gitCommonDir = () => {
    const result = spawnSync('git', ['rev-parse', '--git-common-dir'], {
      cwd: repoRoot,
      encoding: 'utf8',
    })
    return result.status === 0 ? result.stdout.trim() : ''
  },
} = {}) {
  const local = join(repoRoot, '.venv', 'bin', 'python3')
  if (exists(local) && isReady(local)) return local

  const commonDir = gitCommonDir()
  if (commonDir) {
    const commonGitDir = resolve(repoRoot, commonDir)
    const commonCheckout = dirname(commonGitDir)
    const shared = join(commonCheckout, '.venv', 'bin', 'python3')
    if (exists(shared) && isReady(shared)) return shared
  }

  throw new Error(
    'QA boot requires Python at this checkout/.venv/bin/python3 or its linked repository checkout/.venv/bin/python3',
  )
}

/**
 * Refuse to boot against anything that could be live. Throws on violation.
 */
export function assertSafeBoot({ port, dataDir, cacheDir, logDir }) {
  if (Number(port) === 8080) {
    throw new Error(`SAFETY: refusing to boot on the live dev port 8080 (got ${port})`)
  }
  const realTmp = tmpdir()
  for (const [label, dir] of [
    ['DATA_DIR', dataDir],
    ['CACHE_DIR', cacheDir],
    ['LOGS_DIR', logDir],
  ]) {
    if (!dir || !dir.startsWith(realTmp)) {
      throw new Error(`SAFETY: ${label} (${dir}) is not under the system tmp dir (${realTmp})`)
    }
    if (dir.includes(LIVE_DATA_SEGMENT)) {
      throw new Error(`SAFETY: ${label} (${dir}) points at the live data location`)
    }
  }
}

/**
 * Ensure frontend/dist exists; build it once with vite if absent.
 * The backend serves dist at "/", so the QA agent needs it present.
 */
export function ensureDist({ log = console.log } = {}) {
  if (existsSync(join(DIST_DIR, 'index.html'))) {
    log('[boot] frontend/dist present — reusing existing build')
    return
  }
  log('[boot] frontend/dist missing — running `vite build` once…')
  const res = spawnSync('npx', ['vite', 'build'], {
    cwd: join(REPO_ROOT, 'frontend'),
    stdio: 'inherit',
  })
  if (res.status !== 0) throw new Error('vite build failed — cannot serve the SPA')
  if (!existsSync(join(DIST_DIR, 'index.html'))) {
    throw new Error('vite build produced no dist/index.html')
  }
}

/**
 * Seed the fabricated demo dataset into the disposable DATA_DIR using the repo's
 * own seeder (the same code path /api/demo/seed uses), so the QA agent sees a
 * populated app. Pure fixtures only — never touches the operator's live stores.
 */
export function seedDemo({ dataDir, env, log = console.log, spawnSyncFn = spawnSync }) {
  log('[boot] seeding demo dataset into disposable DATA_DIR…')
  // Seed after creating the schema in the SAME isolated environment.  The
  // examples include DB-backed instruments, so invoking the seeder before the
  // app's lifespan migration creates an uninitialised sqlite file.  Passing
  // `env` is equally important: the seeder is a separate process and otherwise
  // falls back to backend/data despite receiving --dest for JSON files.
  const seedProgram = [
    'import sys',
    'from pathlib import Path',
    'from backend.db.migrate import ensure_migrated',
    'from backend.scripts.demo.build import build',
    'ensure_migrated()',
    'build(offline=True)',
  ].join('; ')
  const res = spawnSyncFn(resolveQaPython(), ['-c', seedProgram, dataDir], {
    cwd: REPO_ROOT,
    stdio: 'inherit',
    env,
  })
  if (res.status !== 0) throw new Error('demo seeding failed')
}

export function buildQaChildEnv({
  sourceEnv = process.env,
  dataDir,
  cacheDir,
  logDir,
  demoMode,
  port,
}) {
  return {
    // A disposable backend has no reason to inherit the operator's complete
    // environment. In particular, backend.main loads `.env` on import, so
    // disable that loading as well as omitting every parent credential. Keep
    // only process plumbing needed by the selected Python interpreter.
    PATH: sourceEnv.PATH || '/usr/bin:/bin',
    LANG: sourceEnv.LANG || 'C.UTF-8',
    LC_ALL: sourceEnv.LC_ALL || 'C.UTF-8',
    LC_CTYPE: sourceEnv.LC_CTYPE || 'C.UTF-8',
    HOME: dirname(cacheDir),
    TMPDIR: dirname(cacheDir),
    TEMP: dirname(cacheDir),
    TMP: dirname(cacheDir),
    PYTHONNOUSERSITE: '1',
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONUNBUFFERED: '1',
    PYTHON_DOTENV_DISABLED: 'true',
    DATA_DIR: dataDir,
    CACHE_DIR: cacheDir,
    // `.env` can hold any of these settings; override each one so a child
    // process never inherits a real runtime directory or sqlite database.
    LOGS_DIR: logDir,
    ONW_DATABASE_URL: `sqlite:///${join(dataDir, 'onw.db')}`,
    ONW_QA_MODE: 'true',
    DEMO_MODE: demoMode ? 'true' : 'false',
    ONW_BASE_URL: '',
    HOST: '127.0.0.1',
    PORT: String(port),
    AUTH_DISABLED: 'true',
    AUTH_API_TOKEN: '',
    AUTH_PASSWORD: '',
    AUTH_PASSWORD_HASH: '',
    AUTH_SESSION_KEY: '',
    ONW_API_TOKEN: '',
    ONW_DISABLE_TELEGRAM: 'true',
    ONW_DISABLE_SLACK: 'true',
    ONW_DISABLE_DISCORD: 'true',
  }
}

async function waitForHealth(baseUrl, { timeoutMs = 30000, log = console.log } = {}) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(2000) })
      if (r.ok) {
        log(`[boot] backend healthy at ${baseUrl}`)
        return
      }
    } catch {
      /* not up yet */
    }
    await new Promise((res) => setTimeout(res, 400))
  }
  throw new Error(`backend did not become healthy within ${timeoutMs}ms`)
}

/**
 * A foreign server can answer /api/health while our child has failed to bind.
 * Wait for this exact child to report Uvicorn's bound-address line before
 * accepting its health response, otherwise a stale process can make QA pass.
 */
async function waitForOwnBackend(child, baseUrl, { timeoutMs = 30000 } = {}) {
  const expected = `Uvicorn running on ${baseUrl}`
  let output = ''
  return new Promise((resolve, reject) => {
    let settled = false
    const finish = (callback, value) => {
      if (settled) return
      settled = true
      clearTimeout(timeout)
      child.stdout.off('data', onData)
      child.stderr.off('data', onData)
      child.off('exit', onExit)
      child.off('error', onError)
      callback(value)
    }
    const onData = (chunk) => {
      output += chunk.toString()
      if (output.includes(expected)) finish(resolve)
    }
    const onExit = (code, signal) => finish(
      reject,
      new Error(`backend exited before it became ready (code ${code}, signal ${signal ?? 'none'})`),
    )
    const onError = (error) => finish(reject, error)
    const timeout = setTimeout(
      () => finish(reject, new Error(`backend did not bind ${baseUrl} within ${timeoutMs}ms`)),
      timeoutMs,
    )
    child.stdout.on('data', onData)
    child.stderr.on('data', onData)
    child.once('exit', onExit)
    child.once('error', onError)
    if (child.exitCode !== null) onExit(child.exitCode, child.signalCode)
  })
}

/**
 * Boot a disposable demo instance. Returns { baseUrl, stop } — call stop() in a
 * finally to kill the child. Asserts safety BEFORE spawning anything.
 *
 * If dataDir is provided, it skips demo seeding and uses the provided data dir directly.
 */
export async function bootDisposableInstance({
  port = 8765,
  dataDir: externalDataDir,
  log = console.log,
} = {}) {
  // cache + logs always live in a fresh isolated root; when an external (cloned)
  // dataDir is provided we use it directly as DATA_DIR (it sits outside this root).
  const root = mkdtempSync(join(tmpdir(), 'onw-qa-'))
  const dataDir = externalDataDir || join(root, 'data')
  const cacheDir = join(root, 'cache')
  const logDir = join(root, 'logs')

  assertSafeBoot({ port, dataDir, cacheDir, logDir })
  log(`[boot] disposable root: ${root}`)

  const env = buildQaChildEnv({
    dataDir,
    cacheDir,
    logDir,
    demoMode: !externalDataDir,
    port,
  })

  ensureDist({ log })
  if (!externalDataDir) {
    seedDemo({ dataDir, env, log })
  } else {
    log('[boot] using external data directory (skipping demo seed)')
  }

  log(`[boot] spawning backend on 127.0.0.1:${port}…`)
  const child = spawn(resolveQaPython(), ['-m', 'backend.main'], {
    cwd: REPO_ROOT,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  child.stdout.on('data', (d) => process.stdout.write(`[backend] ${d}`))
  child.stderr.on('data', (d) => process.stderr.write(`[backend] ${d}`))

  let exited = false
  child.on('exit', (code) => {
    exited = true
    log(`[boot] backend process exited (code ${code})`)
  })

  const baseUrl = `http://127.0.0.1:${port}`
  const stop = () => {
    if (!exited && child.pid) {
      log('[boot] tearing down disposable backend…')
      try {
        child.kill('SIGKILL')
      } catch {
        /* already gone */
      }
    }
  }

  try {
    await waitForOwnBackend(child, baseUrl)
    await waitForHealth(baseUrl, { log })
  } catch (e) {
    stop()
    throw e
  }

  return { baseUrl, stop, root, dataDir, cacheDir, logDir }
}
