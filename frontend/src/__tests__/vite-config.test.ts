import { readFile } from 'node:fs/promises'
import { loadConfigFromFile } from 'vite'
import { describe, expect, it } from 'vitest'

describe('Vite dev server host allowlist', () => {
  // A self-hoster's own hostname is personal data — it names its operator and points at a
  // server holding a complete financial book — so it is supplied by the environment, never
  // committed. What must hold is the MERGE, not any particular host.
  it('merges hostnames supplied via VITE_EXTRA_ALLOWED_HOSTS', async () => {
    const prev = process.env.VITE_EXTRA_ALLOWED_HOSTS
    process.env.VITE_EXTRA_ALLOWED_HOSTS = 'first.example, second.example'
    try {
      const loaded = await loadConfigFromFile(
        { command: 'serve', mode: 'development' },
        new URL('../../vite.config.ts', import.meta.url).pathname,
      )

      expect(loaded?.config.server?.allowedHosts).toContain('first.example')
      expect(loaded?.config.server?.allowedHosts).toContain('second.example')
    } finally {
      if (prev === undefined) delete process.env.VITE_EXTRA_ALLOWED_HOSTS
      else process.env.VITE_EXTRA_ALLOWED_HOSTS = prev
    }
  })

  // Asserted against the config SOURCE, not the loaded config: a developer's own
  // `.env.local` legitimately adds their hostname at runtime, and that must not fail the
  // suite. The invariant worth protecting is that nothing personal is ever COMMITTED.
  it('commits no personal hostname in the config source', async () => {
    const source = await readFile(new URL('../../vite.config.ts', import.meta.url), 'utf8')

    // Distinct set: the public demo host legitimately appears in both the server and the
    // preview allowlist.
    const committedHosts = new Set(
      [...source.matchAll(/'([\w.-]*opennetworth\.ai)'/g)].map((m) => m[1]),
    )
    expect([...committedHosts]).toEqual(['demo.opennetworth.ai'])
  })

  it('allows the public demo.opennetworth.ai hostname', async () => {
    const loaded = await loadConfigFromFile(
      { command: 'serve', mode: 'development' },
      new URL('../../vite.config.ts', import.meta.url).pathname,
    )

    expect(loaded?.config.server?.allowedHosts).toContain('demo.opennetworth.ai')
  })
})

describe('Vite preview host allowlist', () => {
  it('allows the public demo.opennetworth.ai hostname', async () => {
    const loaded = await loadConfigFromFile(
      { command: 'serve', mode: 'production' },
      new URL('../../vite.config.ts', import.meta.url).pathname,
    )

    expect(loaded?.config.preview?.allowedHosts).toContain('demo.opennetworth.ai')
  })
})
