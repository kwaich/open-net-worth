// Settings → Security, Private Cloud only: who is signed in, the way back to the portal, and the
// two ways to sign out.
import { useState } from 'react'
import { useGatewayIdentity } from '../../../../hooks/useGatewayIdentity'
import {
  SIGN_OUT_COPY,
  signOutOfGateway,
  type SignOutScope,
} from '../../../../utils/gatewaySignOut'
import { Btn, Panel, row } from '../ds'
import { ConfirmDialog } from '../../../ui/ConfirmDialog'

export function GatewayAccountPanel() {
  const identity = useGatewayIdentity(true)
  const [confirming, setConfirming] = useState<SignOutScope | null>(null)
  const copy = confirming ? SIGN_OUT_COPY[confirming] : null

  return (
    <Panel
      title="Private Cloud account"
      sub="You sign in to this app through your Private Cloud account"
    >
      {row(
        'Signed in as',
        identity?.portal_url
          ? 'Billing, version and account settings live in the portal.'
          : undefined,
        <span className="rd-gw-identity">
          {identity?.email ?? '…'}
          {identity?.portal_url && (
            <>
              {' · '}
              <a href={identity.portal_url} target="_blank" rel="noopener noreferrer">
                Private Cloud portal
              </a>
            </>
          )}
        </span>,
        { className: 'rd-gw-identity-row' },
      )}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Btn onClick={() => setConfirming('app')}>{SIGN_OUT_COPY.app.label}</Btn>
        <Btn variant="danger" onClick={() => setConfirming('everywhere')}>
          {SIGN_OUT_COPY.everywhere.label}
        </Btn>
      </div>
      <ConfirmDialog
        data={
          copy && confirming
            ? { title: copy.title, body: copy.body, confirmLabel: 'Sign out', danger: true }
            : null
        }
        onConfirm={() => confirming && signOutOfGateway(confirming)}
        onCancel={() => setConfirming(null)}
      />
    </Panel>
  )
}
