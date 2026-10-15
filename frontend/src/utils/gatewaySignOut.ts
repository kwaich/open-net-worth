// Private Cloud sign-out. The gateway owns sign-out at POST /_gateway/logout (same-origin,
// Origin-checked, so a link or image on another site cannot sign anyone out). It answers with a
// redirect to the portal, so this is a real form submission — a navigation — not a fetch.
export type SignOutScope = 'app' | 'everywhere'

export const GATEWAY_LOGOUT_PATH = '/_gateway/logout'

export function signOutOfGateway(scope: SignOutScope): void {
  const form = document.createElement('form')
  form.method = 'POST'
  form.action = GATEWAY_LOGOUT_PATH
  const field = document.createElement('input')
  field.type = 'hidden'
  field.name = 'scope'
  field.value = scope
  form.appendChild(field)
  document.body.appendChild(form)
  form.submit()
}

export const SIGN_OUT_COPY: Record<SignOutScope, { label: string; title: string; body: string }> = {
  app: {
    label: 'Sign out of this app',
    title: 'Sign out of this app?',
    body: 'You will be taken to your Private Cloud portal. You stay signed in to the portal.',
  },
  everywhere: {
    label: 'Sign out everywhere',
    title: 'Sign out everywhere?',
    body: 'This signs you out of the app and the Private Cloud portal, on this device and your other browsers.',
  },
}
