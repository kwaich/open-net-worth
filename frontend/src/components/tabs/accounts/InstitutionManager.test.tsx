// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { InstitutionManager } from './InstitutionManager'

afterEach(cleanup)

const institution = { id: 'inst_dbs', name: 'DBS', accountCount: 2 }

describe('InstitutionManager', () => {
  it('reports the old and new labels after a shared rename', async () => {
    const onRenamed = vi.fn()
    render(
      <InstitutionManager
        open
        institutions={[institution]}
        onClose={() => {}}
        onRename={async () => ({ ...institution, name: 'DBS Bank' })}
        onDelete={async () => ({
          id: institution.id,
          unlinked_account_count: 2,
          unlinked_account_ids: [],
        })}
        onRenamed={onRenamed}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Rename' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Institution name for DBS' }), {
      target: { value: 'DBS Bank' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(onRenamed).toHaveBeenCalledWith(institution, { ...institution, name: 'DBS Bank' }),
    )
  })
})
