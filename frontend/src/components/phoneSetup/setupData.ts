// What /phone-setup shows, from GET /api/shortcuts/setup?t=… (see PhoneSetupPage).

export interface SetupShortcut {
  slug: string
  name: string
  summary: string
  asks: string
  install_url: string | null
}

export interface SetupData {
  setup_code: string
  expires_in: number
  shortcuts: SetupShortcut[]
}

// ONE request per page load. The first view binds the ticket to this device (an HttpOnly cookie
// set by that response); a second concurrent request carries no cookie yet and is refused as
// "another device" — React StrictMode's double effect did exactly that in development.
let pending: Promise<SetupData> | null = null

export function loadSetup(t: string): Promise<SetupData> {
  pending ??= fetch(`/api/shortcuts/setup?t=${encodeURIComponent(t)}`, {
    credentials: 'same-origin',
  }).then(async (r) => {
    if (!r.ok) throw new Error(String(r.status))
    return (await r.json()) as SetupData
  })
  return pending
}

/** Tests only: forget the per-load request. */
export function resetPhoneSetupForTests() {
  pending = null
}
