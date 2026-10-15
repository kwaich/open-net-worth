import assert from 'node:assert/strict';
import { test } from 'node:test';
import { FIRST_POLL_MAX_AGE_MS, MAX_INDIVIDUAL, notificationContent, selectBanners, type AlertEvent, type Banner } from '../src/main/notifications';

const row = (id: string, title: string, summary = '', created_at = `2026-10-03T0${id}:00:00`): Banner => ({ id, read: false, title, summary, created_at, target: 'activity' });

test('a single alert shows its own title and summary', () => {
  assert.deepEqual(notificationContent([row('1', 'Groceries is overspent', 'Spent 412 of 350 this month.')]), [
    { title: 'Groceries is overspent', body: 'Spent 412 of 350 this month.', target: 'activity' },
  ]);
});

test('a few alerts show one banner each, oldest first', () => {
  const shown = notificationContent([row('3', 'Third'), row('1', 'First'), row('2', 'Second')]);
  assert.deepEqual(shown.map(n => n.title), ['First', 'Second', 'Third']);
  assert.equal(shown[0].body, 'Open Activity for details.');
});

test('a backlog collapses into one summary naming the newest alerts', () => {
  const rows = ['1', '2', '3', '4', '5', '6'].map(id => row(id, `Alert ${id}`));
  const shown = notificationContent(rows);
  assert.equal(shown.length, 1);
  assert.equal(shown[0].title, '6 new alerts');
  assert.equal(shown[0].body, `Alert 6 · Alert 5 · Alert 4 and ${6 - MAX_INDIVIDUAL} more`);
});

test('an alert without a title still says something useful, and long text is clipped', () => {
  assert.deepEqual(notificationContent([{ id: 'x', read: false, target: 'activity' }]), [{ title: 'Open Net Worth', body: 'You have new activity to review.', target: 'activity' }]);
  const [long] = notificationContent([row('1', 'T', 'word '.repeat(100))]);
  assert.ok(long.body.length <= 180 && long.body.endsWith('…'));
});

const NOW = Date.parse('2026-10-03T12:00:00Z');
const minutesAgo = (m: number) => new Date(NOW - m * 60_000).toISOString();
const alert = (id: string, ago: number, title = `Alert ${id}`): AlertEvent => ({ id, kind: 'price_alert', title, summary: 'It moved.', created_at: minutesAgo(ago) });

test('log rows banner while unread and undelivered; alerts banner under a namespaced id', () => {
  const log = [{ id: 'a1', read: false, title: 'Same raw id as the alert' }, { id: 'n2', read: true }, { id: 'n3', read: false, title: 'Fresh row' }];
  const { banners, delivered } = selectBanners(log, [alert('a1', 1)], new Set(['n3']), NOW, false);
  assert.deepEqual(banners.map(b => [b.id, b.target]), [['a1', 'activity'], ['alert:a1', 'intel']]);
  assert.deepEqual(delivered, ['a1', 'alert:a1']);
});

test('an alert already delivered is never shown again', () => {
  const { banners, delivered } = selectBanners([], [alert('a1', 1), alert('a2', 2)], new Set(['alert:a1']), NOW, false);
  assert.deepEqual(banners.map(b => b.id), ['alert:a2']);
  assert.deepEqual(delivered, ['alert:a2']);
});

test('the first poll records the whole backlog as delivered but only banners recent alerts', () => {
  const feed = [alert('new', 2), alert('old', 600), alert('edge', FIRST_POLL_MAX_AGE_MS / 60_000 + 1)];
  const first = selectBanners([], feed, new Set(), NOW, true);
  assert.deepEqual(first.banners.map(b => b.id), ['alert:new']);
  assert.deepEqual(first.delivered.sort(), ['alert:edge', 'alert:new', 'alert:old']);
  const second = selectBanners([], feed, new Set(first.delivered), NOW, false);
  assert.deepEqual(second.banners, []);
});

test('after the first poll an older alert is still news (the computer was asleep)', () => {
  const { banners } = selectBanners([], [alert('old', 600)], new Set(), NOW, false);
  assert.deepEqual(banners.map(b => b.id), ['alert:old']);
});

test('an alert with an unparseable time is not bannered on the first poll', () => {
  const bad = { ...alert('x', 1), created_at: 'not a date' };
  assert.deepEqual(selectBanners([], [bad], new Set(), NOW, true).banners, []);
});

test('alerts alone open Intel; mixed with log rows they open Activity', () => {
  const alerts = [alert('a1', 3), alert('a2', 2)];
  const only = selectBanners([], alerts, new Set(), NOW, false).banners;
  assert.ok(notificationContent(only).every(n => n.target === 'intel'));
  const manyAlerts = ['1', '2', '3', '4'].map(id => alert(id, Number(id)));
  assert.deepEqual(notificationContent(selectBanners([], manyAlerts, new Set(), NOW, false).banners).map(n => n.target), ['intel']);
  const mixed = selectBanners([{ id: 'n1', read: false, title: 'Row' }], manyAlerts, new Set(), NOW, false).banners;
  assert.deepEqual(notificationContent(mixed).map(n => n.target), ['activity']);
});
