import type { ProposedChange } from '../../../../types/chat'
import type { CardFixture } from './types'

// Shapes mirror backend `stage_generic` (tool_permission, display {tool, arguments, preview?})
// and `registry.stage_batch` (<kind>_batch, display {op, count, rows}).

function permission(
  tool: string,
  toolUseId: string,
  display: Record<string, unknown>,
): ProposedChange {
  return {
    type: 'tool_permission',
    id: toolUseId,
    grant_tool: tool,
    session_id: 'sess_demo',
    reason: `${tool} needs your approval before it runs.`,
    display: { tool, ...display },
  }
}

function batch(
  type: string,
  grantTool: string,
  toolUseId: string,
  display: Record<string, unknown>,
): ProposedChange {
  return {
    type,
    id: toolUseId,
    grant_tool: grantTool,
    session_id: 'sess_demo',
    display,
  }
}

const PREAMBLE = Array.from({ length: 6 }, (_, i) => `Step ${i + 1}: review the ledger`)
const POSTAMBLE = Array.from({ length: 6 }, (_, i) => `Step ${i + 8}: post the summary`)
const longPrompt = (middle: string) => [...PREAMBLE, middle, ...POSTAMBLE].join('\n')

export const GENERIC_FIXTURES: CardFixture[] = [
  {
    name: 'tool_permission · flat arguments',
    change: permission('record_release', 'toolu_release', {
      arguments: {
        vest_event_id: 'vest_0ffde305',
        release_date: '2026-10-09',
        note: 'Released at Meridian Wealth',
      },
    }),
  },
  {
    name: 'tool_permission · nested object and list',
    change: permission('update_envelopes', 'toolu_envelope', {
      arguments: {
        envelope_id: 'env_4c9a1b2e7d10',
        dry_run: false,
        tags: ['housing', 'fixed'],
        filter: { account: 'Everyday Checking' },
        changes: {
          name: 'Groceries',
          target_amount: 600,
          target_cadence: 'monthly',
          note: 'Raised after the rent review',
        },
      },
    }),
  },
  {
    name: 'tool_permission · list of records',
    change: permission('create_categories', 'toolu_categories', {
      arguments: {
        categories: [
          { label: 'Pets', color: '#7a9cc6' },
          { label: 'Gifts', color: '#c69c7a' },
        ],
      },
    }),
  },
  {
    name: 'tool_permission · no arguments',
    change: permission('scan_intel', 'toolu_scan', { arguments: {} }),
  },
  {
    name: 'tool_permission · agent prompt edit',
    change: permission('update_agent', 'toolu_agent', {
      arguments: {
        agent_id: 'transaction-trade-logger',
        changes: {
          prompt: {
            old: 'Keep the raw bank note.\nUse one external reference.',
            new: 'Keep the raw bank note.\nUse every matching external reference.',
          },
        },
      },
    }),
  },
  {
    name: 'tool_permission · skill text edit',
    change: permission('update_skill', 'toolu_skill', {
      arguments: {
        skill_id: 'month-end-close',
        changes: {
          description: { old: 'Close the month', new: 'Close the month and post the summary' },
          instructions: {
            old: longPrompt('Step 7: flag stale balances'),
            new: longPrompt('Step 7: flag stale balances and unmatched transfers'),
          },
        },
      },
    }),
  },
  {
    name: 'tool_permission · update_automations field diff',
    change: permission('update_automations', 'toolu_auto', {
      preview: {
        kind: 'field_diff',
        data: {
          items: [
            {
              id: 'auto_123',
              name: 'Month-end close',
              baseline_updated_at: '2026-08-01T00:00:00Z',
              changes: {
                name: { old: 'Month close', new: 'Month-end close' },
                paused: { old: false, new: true },
                prompt: {
                  old: longPrompt('Step 7: flag stale balances'),
                  new: longPrompt('Step 7: flag stale balances and unmatched transfers'),
                },
              },
            },
          ],
        },
        raw_arguments: { updates: [{ id: 'auto_123', paused: true }] },
      },
    }),
  },
  {
    name: 'tool_permission · update_automations no changes',
    change: permission('update_automations', 'toolu_auto_same', {
      preview: {
        kind: 'field_diff',
        data: {
          items: [
            {
              id: 'auto_123',
              name: 'Month-end close',
              baseline_updated_at: '2026-08-01T00:00:00Z',
              changes: {},
            },
          ],
        },
        raw_arguments: { updates: [{ id: 'auto_123' }] },
      },
    }),
  },
  {
    name: 'unknown future type · display',
    change: {
      type: 'frobnicate_widget',
      id: 'toolu_future',
      grant_tool: 'frobnicate_widgets',
      // A future tool's display carries keys PositionDisplay does not model — the point of this case.
      display: { gizmo_name: 'Acme 3000', count: 4, targets: ['alpha', 'beta'], dry_run: true },
    } as ProposedChange,
  },
  {
    name: 'unknown future type · empty payload',
    change: {
      type: 'mystery_op',
      id: 'toolu_mystery',
      grant_tool: 'mystery_ops',
    },
  },
  {
    name: 'lot_dispose_batch · three sales',
    change: batch('lot_dispose_batch', 'dispose_lots', 'toolu_dispose_batch', {
      op: 'Record disposals',
      count: 3,
      rows: [
        { position_id: 'pos_a1b2c3d4e5f6', quantity: 10, proceeds_per_unit: 610 },
        { position_id: 'pos_9f8e7d6c5b4a', quantity: 2.5, proceeds_per_unit: 188.4 },
        { position_id: 'pos_0a1b2c3d4e5f', quantity: 40, proceeds_per_unit: null },
      ],
    }),
  },
  {
    name: 'update_batch · position edits',
    change: batch('update_batch', 'update_positions', 'toolu_update_batch', {
      op: 'Update positions',
      count: 2,
      rows: [
        { position_id: 'pos_a1b2c3d4e5f6', changes: { quantity: 12, note: 'Rounded up' } },
        {
          position_id: 'pos_9f8e7d6c5b4a',
          changes: { cost_basis: 4120.5, cost_currency: 'USD', bucket: 'core', note: 'Moved' },
        },
      ],
    }),
  },
  {
    name: 'remove_batch · more rows than shown',
    change: batch('remove_batch', 'remove_positions', 'toolu_remove_batch', {
      op: 'Remove positions',
      count: 14,
      rows: Array.from({ length: 12 }, (_, i) => ({
        position_id: `pos_${String(i).padStart(12, '0')}`,
      })),
    }),
  },
  // Envelope operations (backend/ai/tools/envelopes.py) have no bespoke body: they fall to the
  // generic one, titled by their staged `op`, naming the envelope the backend resolved.
  {
    name: 'envelope_delete · named envelope',
    change: {
      id: 'toolu_envelope_delete',
      type: 'envelope_delete',
      grant_tool: 'delete_envelope',
      display: { op: 'Delete envelope', envelope_id: 'env_dining', envelope_name: 'Dining' },
    } as ProposedChange,
  },
  {
    name: 'envelope_merge · two named envelopes',
    change: {
      id: 'toolu_envelope_merge',
      type: 'envelope_merge',
      grant_tool: 'merge_envelopes',
      display: {
        op: 'Merge envelopes',
        source_envelope_id: 'env_takeaway',
        source_envelope_name: 'Takeaway',
        target_envelope_id: 'env_dining',
        target_envelope_name: 'Dining',
      },
    } as ProposedChange,
  },
  // Budget / envelope batches carry their own display (no `rows`): the generic body reads them.
  {
    name: 'group_delete_batch · two named groups',
    change: {
      id: 'toolu_group_delete_batch',
      type: 'group_delete_batch',
      grant_tool: 'delete_envelope_groups',
      display: {
        op: 'Delete group (batch)',
        count: 2,
        group_ids: ['grp_extras', 'grp_fun'],
        group_names: ['Extras', 'Fun'],
      },
    } as ProposedChange,
  },
  {
    name: 'move_category_batch · envelopes into a group',
    change: {
      id: 'toolu_move_category_batch',
      type: 'move_category_batch',
      grant_tool: 'move_envelopes_to_group',
      display: {
        op: 'Move category (batch)',
        count: 2,
        categories: ['env_streaming', 'env_cinema'],
        category_names: ['Streaming', 'Cinema'],
        group_id: 'grp_fun',
        group_name: 'Fun',
      },
    } as ProposedChange,
  },
  {
    name: 'envelope_update_batch · renames and a note',
    change: {
      id: 'toolu_envelope_update_batch',
      type: 'envelope_update_batch',
      grant_tool: 'update_envelopes',
      display: {
        op: 'Update envelopes',
        updates: [
          { envelope_id: 'env_dining', envelope_name: 'Dining', name: 'Eating out' },
          { envelope_id: 'env_travel', envelope_name: 'Travel Fund', note: 'Japan in March' },
        ],
      },
    } as ProposedChange,
  },
  {
    name: 'envelope_add_category_batch · two categories linked',
    change: {
      id: 'toolu_envelope_add_category_batch',
      type: 'envelope_add_category_batch',
      grant_tool: 'link_categories',
      display: {
        op: 'Link categories',
        envelope_id: 'env_dining',
        envelope_name: 'Dining',
        category_ids: ['cat_cafe0000aaaa', 'cat_bar00000bbbb'],
        category_names: ['Cafes', 'Bars'],
      },
    } as ProposedChange,
  },
  // What a real budget write shows: the dispatch gate's card, with `_budget_confirm`'s
  // named-args preview naming each envelope.
  {
    name: 'assign_budget · gate card with envelope names',
    change: permission('assign_budget', 'toolu_assign_named', {
      arguments: {
        assignments: [
          { envelope_id: 'env_5f8a1', amount: 25, month: '2026-10' },
          { envelope_id: 'env_9c2d4', amount: 10, month: '2026-10' },
        ],
      },
      preview: {
        kind: 'named_args',
        data: {
          assignments: [
            {
              envelope_id: 'env_5f8a1',
              envelope_name: 'Travel Fund',
              amount: 25,
              month: '2026-10',
            },
            {
              envelope_id: 'env_9c2d4',
              envelope_name: 'Emergency Fund',
              amount: 10,
              month: '2026-10',
            },
          ],
        },
        raw_arguments: {
          assignments: [
            { envelope_id: 'env_5f8a1', amount: 25, month: '2026-10' },
            { envelope_id: 'env_9c2d4', amount: 10, month: '2026-10' },
          ],
        },
      },
    }),
  },
]
