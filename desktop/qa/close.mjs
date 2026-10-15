import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { _electron as electron } from "playwright";

const repo = path.resolve("..");
const root = await mkdtemp(path.join(tmpdir(), "onw-close-"));
const app = await electron.launch({
  args: [process.cwd()],
  env: { ...process.env, ONW_QA_MODE: "1", ONW_DESKTOP_ROOT: root,
    ONW_DESKTOP_REPO: repo, ONW_DESKTOP_PYTHON: path.join(repo, ".venv/bin/python3") },
  timeout: 45000,
});
const checks = [];
try {
  const page = await app.firstWindow();
  await app.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()[0].setPosition(-2500, -2500);
  });
  await page.waitForURL(/127\.0\.0\.1/, { timeout: 60000 });
  await page.getByRole("heading", { name: "Choose your app currency." }).waitFor();
  const owner = JSON.parse(await readFile(path.join(root, ".instance.lock"), "utf8"));
  const origin = new URL(page.url()).origin;
  await page.evaluate(() => new Promise((resolve, reject) => {
    window.closeTestEvents = new EventSource("/api/events");
    window.closeTestEvents.onopen = () => resolve();
    window.closeTestEvents.onerror = () => reject(new Error("Live updates did not connect"));
  }));
  await page.evaluate(() => window.onwDesktop.updateSettings({ background: true }));
  const background = await app.evaluate(({ BrowserWindow }) => {
    const window = BrowserWindow.getAllWindows()[0];
    window.close();
    return { visible: window.isVisible(), destroyed: window.isDestroyed() };
  });
  assert.deepEqual(background, { visible: false, destroyed: false });
  assert.ok((await fetch(`${origin}/api/health`)).ok);
  checks.push("background close hides immediately and keeps the owned backend alive");
  await app.evaluate(({ app }) => app.emit("activate"));
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), true);
  checks.push("background window can reopen");
  await page.evaluate(() => window.onwDesktop.updateSettings({ background: false }));
  const process = app.process();
  const exited = new Promise((resolve) => process.once("exit", resolve));
  // macOS keeps the app in the Dock when its window closes; elsewhere closing the last window quits.
  const closeQuits = globalThis.process.platform !== "darwin";
  let started = Date.now();
  if (closeQuits) {
    app.evaluate(({ BrowserWindow }) => { BrowserWindow.getAllWindows()[0].close(); }).catch(() => {});
  } else {
    const foreground = await app.evaluate(({ BrowserWindow }) => {
      const window = BrowserWindow.getAllWindows()[0];
      window.close();
      return { visible: window.isVisible(), destroyed: window.isDestroyed() };
    });
    assert.deepEqual(foreground, { visible: false, destroyed: false });
    assert.ok((await fetch(`${origin}/api/health`)).ok);
    checks.push("macOS close hides the window and keeps the backend with background off too");
    started = Date.now();
    await app.evaluate(({ app }) => app.quit());
  }
  await Promise.race([exited, new Promise((_, reject) => {
    const timer = setTimeout(() => reject(new Error("Quit did not finish")), 25000);
    timer.unref();
  })]);
  const shutdownMs = Date.now() - started;
  console.log(JSON.stringify({ shutdownMs }));
  assert.ok(shutdownMs < 5000, `Idle live-update shutdown took ${shutdownMs}ms`);
  await assert.rejects(fetch(`${origin}/api/health`));
  assert.throws(() => globalThis.process.kill(owner.pid, 0));
  checks.push(closeQuits ? "closing the window with background off quits promptly and releases the backend" : "quit shuts live updates down promptly and releases the backend");
  await mkdir("qa/artifacts", { recursive: true });
  await writeFile("qa/artifacts/close-report.json", JSON.stringify({ checks, shutdownMs, root }, null, 2));
  for (const check of checks) console.log("PASS", check);
} finally {
  await app.evaluate(({ app }) => app.quit()).catch(() => {});
  await app.close().catch(() => {});
}
