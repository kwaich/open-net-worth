import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

// Behind the Private Cloud sign-in the manifest is only reachable with the session cookie, and a
// browser fetches a manifest WITHOUT credentials unless told otherwise — so "add to home screen"
// read the sign-in page instead and the app could not be installed.
it('the manifest link sends credentials', () => {
  const html = readFileSync(resolve(__dirname, '../index.html'), 'utf8')
  expect(html).toMatch(/<link rel="manifest"[^>]*crossorigin="use-credentials"/)
})
