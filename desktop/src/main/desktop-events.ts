// The desktop shell's own diagnostics: one JSON line on stderr per event, and the newest few kept in
// memory so a problem report can show what the shell itself noticed (the book's backend keeps its own log).
const KEPT = 100;
const recent: string[] = [];

export function desktopEvent(event: Record<string, unknown>): void {
  const line = JSON.stringify(event);
  process.stderr.write(`${line}\n`);
  recent.push(`${new Date().toISOString().slice(0, 19)} WARNING  desktop ${line}`);
  if (recent.length > KEPT) recent.shift();
}

export const recentDesktopEvents = (): string[] => [...recent];
