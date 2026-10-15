import { readFileSync as __readFileSync } from "node:fs";
const APP_VERSION = JSON.parse(__readFileSync(new URL("../package.json", import.meta.url), "utf8")).version; // the fixture app must report the real version: the backend refuses a mismatched one
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { _electron as electron } from 'playwright';

const repo = path.resolve('..');
const root = await mkdtemp(path.join(tmpdir(), 'onw-dock-'));
const profile = path.join(root, 'desktop');
const fixture = path.join(root, 'entry');
await mkdir(profile); await mkdir(fixture);
await symlink(path.join(repo, 'desktop/resources'), path.join(fixture, 'resources'));
await writeFile(path.join(fixture, 'package.json'), JSON.stringify({ name: 'onw-dock-qa', version: APP_VERSION, main: 'entry.cjs' }));
await writeFile(path.join(fixture, 'entry.cjs'), `const {app,Tray}=require('electron');app.getLoginItemSettings=()=>({wasOpenedAtLogin:process.env.ONW_DOCK_LOGIN==='1',openAtLogin:true});const setMenu=Tray.prototype.setContextMenu;Tray.prototype.setContextMenu=function(menu){app.qaTray=this;app.qaTrayMenu=menu;return setMenu.call(this,menu)};require(${JSON.stringify(path.join(repo, 'desktop/dist/main/index.js'))});`);
await writeFile(path.join(profile, 'desktop-settings.json'), JSON.stringify({ background: true, launchAtLogin: true, notifications: false, activitySetupComplete: true, selectedBookFolder: root, selectedServerUrl: null, books: {} }));
const env = { ...process.env, ONW_QA_MODE: '1', ONW_DESKTOP_ROOT: root, ONW_DESKTOP_REPO: repo, ONW_DESKTOP_PYTHON: path.join(repo, 'desktop/runtime/python/bin/python3') };
let app;
const checks = [];
const check = name => { checks.push(name); console.log('PASS', name); };
async function dockIs(visible) {
  for (let i=0; i<25; i++) {
    if (await app.evaluate(({ app }) => app.dock.isVisible()) === visible) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`Dock visibility did not become ${visible}`);
}
async function launch(login) {
  app = await electron.launch({ args: [fixture], env: { ...env, ONW_DOCK_LOGIN: login ? '1' : '0' }, timeout: 20000 });
  const page = await app.firstWindow();
  await page.waitForURL(/127\.0\.0\.1/, { timeout: 45000 });
  await page.getByRole('heading', { name: 'Choose your app currency.', exact: true }).waitFor();
  return page;
}
try {
  let page = await launch(false);
  const origin = new URL(page.url()).origin;
  await dockIs(true);
  await page.evaluate(() => sessionStorage.setItem('dock-qa-preserved', 'same-window'));
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
  await dockIs(true);
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), false);
  assert.ok((await fetch(`${origin}/api/health`)).ok);
  await app.evaluate(({ app }) => app.emit('activate'));
  await dockIs(true);
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), true);
  assert.equal(await page.evaluate(() => sessionStorage.getItem('dock-qa-preserved')), 'same-window');
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
  await dockIs(true);
  await page.evaluate(() => window.onwDesktop.updateSettings({ background: false }));
  await app.evaluate(({ app }) => app.emit('activate'));
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
  await dockIs(true);
  assert.ok((await fetch(`${origin}/api/health`)).ok);
  check('macOS X hides only the window, keeps the Dock and backend with background on or off, and reopening preserves the same window');
  await app.close(); app = null;
  page = await launch(true);
  await dockIs(false);
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), false);
  await app.evaluate(({ app }) => app.qaTray.emit('click'));
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), false);
  await dockIs(false);
  await app.evaluate(({ app }) => app.qaTrayMenu.items[0].click());
  await dockIs(true);
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), true);
  check('simulated login stays hidden on tray click; the Open menu action restores the window and Dock');
  await mkdir('qa/artifacts', { recursive: true });
  await writeFile('qa/artifacts/dock-report.json', JSON.stringify({ root, checks, actualMacOSSignIn: 'not tested' }, null, 2));
} finally { await app?.close().catch(() => {}); }
