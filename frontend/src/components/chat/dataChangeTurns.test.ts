import { expect, it } from 'vitest'
import type { Message } from '../../types/chat'
import { dataChangeTurns } from './dataChangeTurns'

const user = (id: string) => ({ id, role: 'user', content: id }) as unknown as Message
const reply = (id: string, receipt = false) =>
  ({
    id,
    role: 'assistant',
    content: '',
    blocks: receipt ? [{ type: 'receipt', receipt: {} }] : [{ type: 'text', text: 'ok' }],
  }) as unknown as Message

it('marks only the user messages whose turn left a write receipt', () => {
  const turns = dataChangeTurns([
    user('name'),
    reply('a1'), // saved the name: no receipt
    user('balances'),
    reply('a2'),
    reply('a3', true), // created the accounts
    user('thanks'),
    reply('a4'),
  ])
  expect([...turns]).toEqual(['balances'])
})
