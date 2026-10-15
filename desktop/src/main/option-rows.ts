const escape = (value: string): string => value.replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]!);

export const ICON_COMPUTER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8M12 17v4"/></svg>';
export const ICON_SERVER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="20" height="8" x="2" y="2" rx="2"/><rect width="20" height="8" x="2" y="14" rx="2"/><path d="M6 6h.01M6 18h.01"/></svg>';
export const ICON_CLOUD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/></svg>';
const CHEVRON = '<svg class="chev" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';

// Title, then what the choice does, then — for the current one — where your book is now.
export function option(id: string, icon: string, title: string, desc: string, current: boolean, where = ''): string {
  return `<button class="option${current ? ' is-current' : ''}" id="${id}" type="button" aria-label="${escape(title)}"><span class="icon">${icon}</span><span class="text"><span class="title">${escape(title)}</span><span class="desc">${escape(desc)}</span>${where ? `<span class="where">${escape(where)}</span>` : ''}</span>${current ? '<span class="tag">Current</span>' : CHEVRON}</button>`;
}

// "~/Documents/My book" instead of "/Users/name/Documents/My book".
export function shortPath(path: string): string {
  const home = process.env.HOME || process.env.USERPROFILE || '';
  return home && path.startsWith(home) ? `~${path.slice(home.length)}` : path;
}

export const OPTION_CSS = `.options{display:flex;flex-direction:column;gap:10px;margin-top:28px}
.option{display:flex;height:auto;justify-content:flex-start;line-height:1.5;align-items:center;gap:16px;width:100%;text-align:left;padding:16px 18px;border:1px solid var(--line);border-radius:10px;background:transparent;font-weight:400}
.option:hover{border-color:var(--fr-gold);background:var(--surface2)}.option .icon{flex:none;width:36px;height:36px;border-radius:8px;display:grid;place-items:center;background:var(--surface2);color:var(--ink)}
.option .icon svg{width:18px;height:18px;color:inherit}.option .text{flex:1;min-width:0}.option .title{display:block;font-weight:600;color:var(--ink)}.option .desc{display:block;color:var(--muted);font-size:var(--text-base);line-height:1.5;overflow-wrap:anywhere}
.option .where{display:block;margin-top:6px;color:var(--ink);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:var(--text-sm);overflow-wrap:anywhere}.option.is-current{border-color:var(--line-strong,var(--line))}.option .tag{flex:none;font-size:var(--text-sm);color:var(--fr-gold);border:1px solid var(--fr-gold);border-radius:999px;padding:2px 10px}.option .chev{flex:none;color:var(--muted)}
.more{margin-top:22px}.more button{border:0;padding:0;color:var(--fr-gold);font-weight:500;text-decoration:underline;text-underline-offset:3px}`;
