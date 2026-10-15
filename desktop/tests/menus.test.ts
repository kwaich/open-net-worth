import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { MenuItemConstructorOptions } from 'electron';
import { GO_TABS, type DesktopCommand } from '../src/contracts';
import { goMenu, helpMenu, viewMenu, windowMenu } from '../src/main/app-menu';
import { safeServerCommand, validCommand } from '../src/main/security';

const idle = { canGoBack: false, canGoForward: false };
const items = (menu: MenuItemConstructorOptions): MenuItemConstructorOptions[] => menu.submenu as MenuItemConstructorOptions[];
const click = (item: MenuItemConstructorOptions | undefined) => (item!.click as () => void)();
const named = (menu: MenuItemConstructorOptions, label: string) => items(menu).find(item => item.label === label);
const view = (platform: NodeJS.Platform, serverMode = false, send: (command: DesktopCommand) => void = () => {}) => viewMenu(platform, { send, reload: () => {}, serverMode });
const help = (platform: NodeJS.Platform, serverMode = false, send: (command: DesktopCommand) => void = () => {}) => helpMenu(platform, { send, checkForUpdates: { label: 'Check for Updates…' }, serverMode, openLogs: () => {}, revealBook: () => {}, restartServer: () => {} });

test('Go lists history first, then one screen per main tab on Cmd/Ctrl+1 to +8', () => {
  const sent: DesktopCommand[] = [];
  const menu = goMenu('darwin', idle, command => sent.push(command));
  assert.deepEqual(items(menu).slice(0, 2).map(item => item.id), ['history-back', 'history-forward']);
  const screens = items(menu).filter(item => item.accelerator?.startsWith('CmdOrCtrl+'));
  assert.deepEqual(screens.map(item => item.label), ['Lucius', 'Assets', 'Accounts', 'Plan', 'Investments', 'Intel', 'Commitments', 'Dashboard']);
  assert.deepEqual(screens.map(item => item.accelerator), [1, 2, 3, 4, 5, 6, 7, 8].map(n => `CmdOrCtrl+${n}`));
  screens.forEach(click);
  assert.deepEqual(sent, GO_TABS.map(tab => ({ type: 'go', tab })));
});

test('Go history items still send history commands', () => {
  const sent: DesktopCommand[] = [];
  const menu = goMenu('win32', { canGoBack: true, canGoForward: true }, command => sent.push(command));
  items(menu).slice(0, 2).forEach(click);
  assert.deepEqual(sent, [{ type: 'history-back' }, { type: 'history-forward' }]);
});

test('View renames Search to Command Palette and adds a sidebar toggle that leaves Cmd+B to the page', () => {
  const sent: DesktopCommand[] = [];
  const menu = view('darwin', false, command => sent.push(command));
  const palette = named(menu, 'Command Palette…')!;
  const sidebar = named(menu, 'Toggle Sidebar')!;
  assert.equal(palette.accelerator, 'CmdOrCtrl+K');
  assert.equal(sidebar.accelerator, 'CmdOrCtrl+B');
  assert.equal(sidebar.registerAccelerator, false);
  assert.equal(named(menu, 'Search'), undefined);
  click(palette);
  click(sidebar);
  assert.deepEqual(sent, [{ type: 'search' }, { type: 'toggle-sidebar' }]);
});

test('View keeps zoom, reload and full screen, and disables document review for a server', () => {
  const roles = items(view('darwin')).map(item => item.role);
  for (const role of ['resetZoom', 'zoomIn', 'zoomOut', 'togglefullscreen']) assert.ok(roles.includes(role as never), role);
  assert.ok(named(view('darwin'), 'Reload'));
  assert.equal(named(view('darwin', false), 'Review documents')!.enabled, true);
  assert.equal(named(view('darwin', true), 'Review documents')!.enabled, false);
});

test('Window adds Show Main Window to the window menu', () => {
  let shown = 0;
  const menu = windowMenu('darwin', () => { shown += 1; });
  assert.equal(menu.role, 'windowMenu');
  click(named(menu, 'Show Main Window'));
  assert.equal(shown, 1);
  assert.ok(items(menu).some(item => item.role === 'front'));
  assert.equal(items(windowMenu('linux', () => {})).some(item => item.role === 'front'), false);
});

test('Help opens the docs and shortcuts, and keeps Check for Updates off macOS only', () => {
  const sent: DesktopCommand[] = [];
  const menu = help('win32', false, command => sent.push(command));
  assert.equal(menu.role, 'help');
  click(named(menu, 'Open Net Worth Help'));
  click(named(menu, 'Keyboard Shortcuts'));
  assert.deepEqual(sent, [{ type: 'open-help' }, { type: 'show-shortcuts' }]);
  assert.ok(named(menu, 'Check for Updates…'));
  assert.equal(named(help('darwin'), 'Check for Updates…'), undefined);
});

test('Keyboard Shortcuts avoids Cmd+/ because the page already uses it to toggle the Lucius panel', () => {
  assert.notEqual(named(help('darwin'), 'Keyboard Shortcuts')!.accelerator, 'CmdOrCtrl+/');
});

test('Troubleshooting disables the book folder and server restart for a server connection', () => {
  const troubleshooting = (serverMode: boolean) => items(named(help('darwin', serverMode), 'Troubleshooting') as MenuItemConstructorOptions);
  const enabled = (serverMode: boolean) => troubleshooting(serverMode).map(item => [item.label, item.enabled ?? true]);
  assert.deepEqual(enabled(false), [['Open Logs Folder', true], ['Reveal Book Folder', true], ['Restart Server', true]]);
  assert.deepEqual(enabled(true), [['Open Logs Folder', true], ['Reveal Book Folder', false], ['Restart Server', false]]);
});

test('no two items in the application menus share an accelerator on a platform', () => {
  for (const platform of ['darwin', 'win32', 'linux'] as const) {
    const menus = [view(platform), goMenu(platform, idle, () => {}), windowMenu(platform, () => {}), help(platform)];
    const accelerators = menus.flatMap(menu => items(menu)).map(item => item.accelerator).filter(Boolean);
    assert.equal(new Set(accelerators).size, accelerators.length, platform);
  }
});

test('only the listed screens may be navigated to, for a local book or a server', () => {
  for (const tab of GO_TABS) assert.equal(validCommand({ type: 'go', tab }), true);
  for (const tab of ['settings', 'inbox', 'build', '../lucius', '', undefined, 3]) assert.equal(validCommand({ type: 'go', tab }), false);
  assert.equal(validCommand({ type: 'search' }), true);
  for (const type of ['go', 'toggle-sidebar', 'open-help', 'show-shortcuts']) assert.equal(safeServerCommand(type), true);
});
