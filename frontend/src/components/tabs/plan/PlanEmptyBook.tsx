// A Plan sub-tab with no accounts to project from — the first-run empty view, shown by both
// Cash flow and Outlook.

import type { ReactNode } from 'react'
import { Button, Cluster, EmptyState } from '../../ui'
import { LuciusCta } from '../../intro/LuciusCta'

const ACCOUNTS_MESSAGE = 'Help me set up my accounts'

export function PlanEmptyBook({
  icon,
  title,
  body,
  onAddAccount,
  onAskLucius,
}: {
  icon: ReactNode
  title: string
  body: string
  onAddAccount: () => void
  onAskLucius?: (text: string) => void
}) {
  return (
    <EmptyState
      fill
      icon={icon}
      title={title}
      body={body}
      action={
        <Cluster gap="--space-3" justify="center">
          <Button variant="primary" size="sm" onClick={onAddAccount}>
            Add account
          </Button>
          <LuciusCta label="Add with Lucius" message={ACCOUNTS_MESSAGE} onAskLucius={onAskLucius} />
        </Cluster>
      }
    />
  )
}
