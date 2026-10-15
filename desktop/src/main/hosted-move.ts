import type { ChecklistItem } from './move-secrets';

function lines(items: ChecklistItem[]): string {
  return items.map(item => `  • ${item.title}${item.detail ? ` (${item.detail})` : ''}: ${item.fix}`).join('\n');
}

// What the person reads when a move ends: only what genuinely could not move, each with its fix.
export function movedDetail(items: ChecklistItem[], direction: 'to-cloud' | 'to-computer'): string {
  const rest = items.length ? `A few things could not move:\n${lines(items)}` : 'Your keys, connections and automations moved with it.';
  const old = direction === 'to-cloud'
    ? 'The book on this computer is untouched, but it is now the old copy: its bots and automations are paused, and it shows a note when you open it. Use File → Move my book back to this computer any time.'
    : 'The copy in your Private Cloud is untouched, but it is now the old copy: its bots and automations are paused.';
  return `${rest}\n\n${old}`;
}

export function whenText(iso: string, locale?: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? 'earlier' : date.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' });
}
