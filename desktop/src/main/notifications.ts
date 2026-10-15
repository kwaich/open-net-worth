import { app, Notification } from 'electron';
import { qaInactiveMode } from './window-chrome';
import { BackendSupervisor } from './backend';
import { Preferences } from './preferences';

export interface ActivityRow { id: string; read: boolean; title?: string; summary?: string; created_at?: string }
// A push-worthy alert from the Intel stories (price alerts, crash watch, explained watch alerts,
// pushed analyses) — they never reach the notification log, so the backend serves them separately.
export interface AlertEvent { id: string; kind: string; title: string; summary: string; created_at: string }
export type BannerTarget = 'activity' | 'intel';
export interface Banner extends ActivityRow { target: BannerTarget }
export interface NotificationContent { title: string; body: string; target: BannerTarget }

const APP = 'Open Net Worth';
// Above this many new alerts at once, one summary instead of a stack of banners.
export const MAX_INDIVIDUAL = 3;
// On the first poll after launch or a book switch, an alert older than this is history, not news.
export const FIRST_POLL_MAX_AGE_MS = 15 * 60_000;
const ALERT_PREFIX = 'alert:';

function clip(text: string, max: number): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat;
}

// What each banner says: the alert's own title and summary, oldest first, or one summary banner
// naming the newest alerts when several arrive together (first launch, a backlog after sleep).
export function notificationContent(fresh: Banner[]): NotificationContent[] {
  const rows = [...fresh].sort((a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? ''));
  if (rows.length <= MAX_INDIVIDUAL) {
    return rows.map(row => {
      const title = clip(row.title ?? '', 80);
      const summary = clip(row.summary ?? '', 180);
      if (title) return { title, body: summary || 'Open Activity for details.', target: row.target };
      return { title: APP, body: summary || 'You have new activity to review.', target: row.target };
    });
  }
  const named = rows.slice(-MAX_INDIVIDUAL).reverse().map(row => clip(row.title ?? '', 60)).filter(Boolean);
  const more = rows.length - named.length;
  const body = named.length ? `${named.join(' · ')}${more > 0 ? ` and ${more} more` : ''}` : 'Open Activity to review them.';
  return [{ title: `${rows.length} new alerts`, body, target: rows.every(row => row.target === 'intel') ? 'intel' : 'activity' }];
}

export interface BannerSelection { banners: Banner[]; delivered: string[] }

// Merges the notification log and the alert feed. Log rows are bannered while unread and not yet
// delivered. Alert events have no read state: each is bannered once, under a namespaced id so it
// cannot collide with a log row. The first poll of a session records every current alert as
// delivered but only banners the recent ones, so a fresh install or a book switch never sprays
// the last day's backlog.
export function selectBanners(log: ActivityRow[], alerts: AlertEvent[], delivered: ReadonlySet<string>, nowMs: number, firstPoll: boolean): BannerSelection {
  const banners: Banner[] = [];
  const seen: string[] = [];
  for (const row of log) {
    if (row.read || delivered.has(row.id)) continue;
    seen.push(row.id);
    banners.push({ ...row, target: 'activity' });
  }
  for (const alert of alerts) {
    const id = `${ALERT_PREFIX}${alert.id}`;
    if (delivered.has(id)) continue;
    seen.push(id);
    const age = nowMs - Date.parse(alert.created_at);
    if (firstPoll && !(age <= FIRST_POLL_MAX_AGE_MS)) continue;
    banners.push({ id, read: false, title: alert.title, summary: alert.summary, created_at: alert.created_at, target: 'intel' });
  }
  return { banners, delivered: seen };
}

export class NativeNotifications {
  private timer: NodeJS.Timeout | null = null;
  private polling: Promise<void> | null = null;
  private generation = 0;
  private primed = false;
  private visible = new Set<Notification>();
  constructor(private readonly backend: BackendSupervisor, private readonly preferences: Preferences, private readonly openActivity: () => void, private readonly openIntel: () => void, private readonly openDocuments: () => void) {}

  start(): void {
    this.stop();
    this.primed = false;
    this.timer = setInterval(() => { void this.poll().catch(() => {}); }, 10_000);
    void this.poll().catch(() => {});
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.generation++;
    for (const notification of this.visible) notification.close();
    this.visible.clear();
  }

  async drain(): Promise<void> { this.stop(); await this.polling?.catch(() => {}); }

  showReview(): void {
    this.show({ title: APP, body: 'New documents are ready to review.' }, this.openDocuments);
  }

  private poll(): Promise<void> {
    if (this.polling || !this.backend.origin) return this.polling ?? Promise.resolve();
    this.polling = this.pollBook(this.generation, this.backend.bookRoot).finally(() => { this.polling = null; });
    return this.polling;
  }

  private async pollBook(generation: number, root: string): Promise<void> {
    const [rows, alerts] = await Promise.all([
      this.backend.json<ActivityRow[]>('/api/notification-log?limit=200'),
      // The alert feed is additive: if it is unavailable the log's banners must still arrive.
      this.backend.json<AlertEvent[]>('/api/desktop/alerts').catch(() => null),
    ]);
    if (generation !== this.generation) return;
    const { banners, delivered } = selectBanners(rows, alerts ?? [], new Set(this.preferences.current.deliveredNotifications), Date.now(), !this.primed);
    if (delivered.length) {
      const next = [...new Set([...this.preferences.current.deliveredNotifications, ...delivered])].slice(-500);
      await this.preferences.saveForBook(root, { deliveredNotifications: next });
      if (generation !== this.generation) return;
    }
    // Only a poll that actually read the feed counts as the first one; otherwise the backlog
    // would arrive as "new" on the next poll.
    if (alerts) this.primed = true;
    for (const content of notificationContent(banners)) this.show(content, content.target === 'intel' ? this.openIntel : this.openActivity);
  }

  private show({ title, body }: Pick<NotificationContent, 'title' | 'body'>, action: () => void): void {
    if (!this.preferences.current.notifications || !Notification.isSupported() || qaInactiveMode(app.isPackaged, process.env)) return;
    const generation = this.generation;
    const notification = new Notification({ title, body, silent: false });
    this.visible.add(notification);
    notification.on('close', () => this.visible.delete(notification));
    notification.on('click', () => { if (generation === this.generation) action(); });
    notification.show();
  }
}
