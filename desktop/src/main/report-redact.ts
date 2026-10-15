// Best-effort scrubbing of a log excerpt before a problem report can show it. A deliberate twin of
// backend/core/report_redact.py (the desktop reads its logs before any book is open, so it cannot
// ask the backend); both run tests/fixtures/problem_report_redaction.json so they cannot drift.
// It narrows what leaves the machine, it does not prove the excerpt clean: the user edits the
// preview before anything is sent.

export const MAX_LOG_LINES = 200;
export const MAX_LINE_CHARS = 400;
const MIN_TERM = 4;

const CURRENCIES = 'USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR|VND|SGD|HKD|NZD|SEK|NOK|DKK|THB|MXN|BRL|KRW|ZAR|PLN|CZK|IDR|MYR|PHP|AED|SAR|TRY|ILS|RUB|TWD';
const NUMBER = '-?\\d[\\d,]*(?:\\.\\d+)?';
const SECRET_KEY = 'api[_-]?key|apikey|token|secret|passw(?:or)?d|authorization|credential|private[_-]?key|access[_-]?key|cookie';
const MONEY_KEY = 'amount|balance|price|value|total|cost|fee|salary|income|net_?worth|quantity|qty|shares|proceeds|payment|deposit|withdrawal|debit|credit';

const URL_USERINFO = /([A-Za-z][A-Za-z0-9+.\-]*:\/\/)[^\s/?#@]+@/g;
const URL_TAIL = /(\bhttps?:\/\/[^\s?#"'<>)]+)[?#][^\s"'<>)]*/g;
const EMAIL = /[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/g;
const AUTH_SCHEME = /\b(Bearer|Basic)[ \t]+[A-Za-z0-9._~+/=\-]{8,}/g;
const SECRET_PAIR = new RegExp(`(?<![A-Za-z0-9])([A-Za-z0-9_.\\-]*(?:${SECRET_KEY})[A-Za-z0-9_.\\-]*)(["']?[ \\t]*[:=][ \\t]*)(\\[[a-z]+\\]|"[^"]*"|'[^']*'|[^\\s"',;&}\\])]+)`, 'gi');
const KNOWN_TOKEN = /\bsk-[A-Za-z0-9_\-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|\bxox[abprs]-[A-Za-z0-9\-]{10,}|\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_\-]{30,}|\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}|\b\d{8,10}:[A-Za-z0-9_\-]{30,}/g;
const LONG_RUN = /(?<![A-Za-z0-9_+=])[A-Za-z0-9_+=]{32,}(?![A-Za-z0-9_+=])/g;
const HOME_UNIX = /\/(?:Users|home)\/[^/\s"']+/g;
const HOME_WINDOWS = /[A-Za-z]:(?:\\+|\/)Users(?:\\+|\/)[^\\/\s"']+/gi;
const MONEY_PREFIXED = new RegExp(`(?:[$€£¥₹₩₫₽₺฿₪]|\\b(?:${CURRENCIES})\\b)[ \\t]?${NUMBER}`, 'g');
const MONEY_SUFFIXED = new RegExp(`${NUMBER}[ \\t]?\\b(?:${CURRENCIES})\\b`, 'g');
const MONEY_PAIR = new RegExp(`([A-Za-z_]*(?:${MONEY_KEY})[A-Za-z_]*["']?[ \\t]*[:=][ \\t]*["']?)(${NUMBER})(?![\\w%])`, 'gi');
const MONEY_GROUPED = /(?<![\w.,:/\-])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w.,])/g;
const MONEY_CENTS = /(?<![\w.:/\-])-?\d+\.\d{2}(?![\w.])/g;
const LINE_START = /^\d{4}-\d{2}-\d{2}T/;
const TRACE_LINE = /^(?:Traceback\b|[ \t]+File "|[ \t]{2,}\S|[\w.]*(?:Error|Exception|Warning)\b)/;

export interface RedactionContext {
  accounts?: readonly string[];
  payees?: readonly string[];
  // A known folder and the token that replaces it, e.g. the book folder and "<book>".
  paths?: readonly (readonly [string, string])[];
}

const escapeRegExp = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function wordPattern(terms: readonly string[] | undefined): RegExp | null {
  const unique = [...new Set((terms ?? []).map(term => term.trim()).filter(term => term.length >= MIN_TERM))].sort((a, b) => b.length - a.length);
  return unique.length ? new RegExp(`(?<!\\w)(?:${unique.map(escapeRegExp).join('|')})(?!\\w)`, 'gi') : null;
}

function replacePaths(text: string, paths: RedactionContext['paths']): string {
  let result = text;
  for (const [path, token] of [...(paths ?? [])].sort((a, b) => b[0].length - a[0].length)) {
    if (path.length < 2) continue;
    for (const variant of new Set([path, path.replaceAll('\\', '\\\\'), path.replaceAll('\\', '/')])) result = result.replace(new RegExp(escapeRegExp(variant), 'gi'), () => token);
  }
  return result;
}

const mixed = (run: string): boolean => /\d/.test(run) && /[A-Za-z]/.test(run);

export function redactText(text: string, context: RedactionContext = {}): string {
  let result = text;
  for (const [pattern, token] of [[wordPattern(context.accounts), '[account]'], [wordPattern(context.payees), '[payee]']] as const) {
    if (pattern) result = result.replace(pattern, () => token);
  }
  result = replacePaths(result, context.paths)
    .replace(HOME_WINDOWS, () => '~')
    .replace(HOME_UNIX, () => '~')
    .replace(URL_USERINFO, (_match, scheme: string) => `${scheme}[redacted]@`)
    .replace(URL_TAIL, (_match, base: string) => `${base}?[redacted]`)
    .replace(EMAIL, () => '[email]')
    .replace(AUTH_SCHEME, (_match, scheme: string) => `${scheme} [redacted]`)
    .replace(SECRET_PAIR, (match, key: string, separator: string, value: string) => /^\[[a-z]+\]$|^\d{1,6}$/.test(value) ? match : `${key}${separator}[redacted]`)
    .replace(KNOWN_TOKEN, () => '[token]')
    .replace(LONG_RUN, run => mixed(run) ? '[token]' : run)
    .replace(MONEY_PREFIXED, () => '[amount]')
    .replace(MONEY_SUFFIXED, () => '[amount]')
    .replace(MONEY_PAIR, (_match, key: string) => `${key}[amount]`)
    .replace(MONEY_GROUPED, () => '[amount]')
    .replace(MONEY_CENTS, () => '[amount]');
  return result;
}

const omission = (count: number): string => `[${count} free-text line${count === 1 ? '' : 's'} omitted]`;

// A line that does not open with a timestamp continues the entry above it: only a traceback shape
// is kept, the rest (a model's reply, a pasted document) collapses into one marker.
export function redactLogLines(lines: readonly string[], context: RedactionContext = {}, limit = MAX_LOG_LINES): string[] {
  const kept: string[] = [];
  let omitted = 0;
  for (const raw of lines) {
    const line = raw.replace(/[\r\n]+$/, '');
    if (!line.trim()) continue;
    if (!LINE_START.test(line) && !TRACE_LINE.test(line)) { omitted += 1; continue; }
    if (omitted) { kept.push(omission(omitted)); omitted = 0; }
    const text = redactText(line, context);
    kept.push(text.length <= MAX_LINE_CHARS ? text : `${text.slice(0, MAX_LINE_CHARS)} …`);
  }
  if (omitted) kept.push(omission(omitted));
  return kept.slice(-limit);
}
