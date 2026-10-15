import { readFileSync as __readFileSync } from "node:fs";
const APP_VERSION = JSON.parse(__readFileSync(new URL("../package.json", import.meta.url), "utf8")).version; // the fixture app must report the real version: the backend refuses a mismatched one
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { _electron as electron } from 'playwright';

const repo = path.resolve('..');
const fixture = await mkdtemp(path.join(tmpdir(), 'onw-startup-stages-'));
const home = path.join(fixture, 'Home');
const profile = path.join(fixture, 'Profile');
for (const folder of [home, profile, 'qa/artifacts']) await mkdir(folder, { recursive: true });
await writeFile(path.join(fixture, 'package.json'), JSON.stringify({ name: 'onw-startup-stages-qa', version: APP_VERSION, main: 'entry.cjs' }));
await writeFile(path.join(fixture, 'entry.cjs'), `const {app}=require('electron');app.setPath('home',${JSON.stringify(home)});app.setPath('userData',${JSON.stringify(profile)});require(${JSON.stringify(path.join(process.cwd(), 'dist/main/index.js'))});`);
const env = { ...process.env, ONW_QA_MODE: '1', ONW_DESKTOP_REPO: repo };
delete env.ONW_DESKTOP_ROOT;
const checks = [];
let app;
const gate = () => {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release: () => release() };
};
const check = name => { checks.push(name); console.log('PASS', name); };
async function launch() {
  app = await electron.launch({ args: [fixture], env, timeout: 45000 });
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().forEach(window => window.setPosition(-2500, -2500)));
  const page = await app.firstWindow();
  page.setDefaultTimeout(20000);
  return page;
}
const appearance = page => page.evaluate(async () => {
  await document.fonts.ready;
  const logo = document.querySelector('.brand-logo, [data-builtin-logo]');
  const bounds = logo.getBoundingClientRect();
  const progress = getComputedStyle(document.querySelector('.loading, .desktop-startup-progress'));
  return {
    size: bounds.width,
    x: bounds.x,
    y: bounds.y,
    font: progress.fontFamily,
    textSize: progress.fontSize,
    color: progress.color,
    background: getComputedStyle(document.body).backgroundColor,
  };
});
try {
  const page = await launch();
  const auth = gate();
  const setup = gate();
  const setupRequested = gate();
  await page.route('**/api/auth/status', async route => { await auth.promise; await route.continue(); });
  await page.route('**/api/setup-status', async route => { setupRequested.release(); await setup.promise; await route.continue(); });
  await page.getByRole('button', { name: 'Get started', exact: true }).click();
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await page.getByRole('status', { name: 'Setting up your book…' }).waitFor();
  const native = await appearance(page);
  await page.screenshot({ path: 'qa/artifacts/startup-creating.png' });
  check('confirming an empty folder says Setting up your book');
  await page.getByRole('status', { name: 'Getting your setup ready…' }).waitFor({ timeout: 60000 });
  const renderer = await appearance(page);
  assert.equal(renderer.size, 64);
  assert.equal(renderer.textSize, native.textSize);
  assert.equal(renderer.color, native.color);
  assert.equal(renderer.background, native.background);
  assert.ok(renderer.font.includes('Plus Jakarta Sans'));
  assert.ok(Math.abs(renderer.x - native.x) < 2);
  assert.ok(Math.abs(renderer.y - native.y) < 2);
  assert.equal(await page.locator('.auth-splash-brand').count(), 0);
  await page.screenshot({ path: 'qa/artifacts/startup-access-check.png' });
  check('access checking keeps the same logo, position, type and palette instead of flashing the app name');
  auth.release();
  await setupRequested.promise;
  await page.getByRole('status', { name: 'Getting your setup ready…' }).waitFor();
  assert.equal(await page.locator('.desktop-startup-splash img').count(), 1);
  assert.equal(await page.locator('.app-boot').count(), 0);
  await page.screenshot({ path: 'qa/artifacts/startup-setup-check.png' });
  check('setup checking keeps the same branded view instead of a blank screen');
  setup.release();
  await page.getByRole('heading', { name: 'Choose your app currency.' }).waitFor();
  assert.equal(await page.getByText('Book folder', { exact: true }).count(), 0);
  check('the handoff ends on the clean currency page');
  await app.close();
  app = null;
  const reopened = await launch();
  await reopened.getByRole('status', { name: 'Opening your book…' }).waitFor();
  await reopened.getByRole('heading', { name: 'Choose your app currency.' }).waitFor({ timeout: 60000 });
  check('reopening the remembered book says Opening your book');
  await writeFile('qa/artifacts/startup-stages-report.json', JSON.stringify({ fixture, checks }, null, 2));
} finally {
  await app?.close();
}
