import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import {
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { _electron as electron } from "playwright";

const repo = path.resolve("..");
const fixture = await mkdtemp(path.join(tmpdir(), "onw-book-picker-"));
const profile = path.join(fixture, "Desktop Profile");
const first = path.join(profile, "book");
const second = path.join(fixture, "Existing Book");
const empty = path.join(fixture, "New Book");
const invalid = path.join(fixture, "Other Files");
const ahead = path.join(fixture, "Newer Book");
const locked = path.join(fixture, "Locked Book");
const watched = path.join(fixture, "Statements");
for (const folder of [
  profile,
  second,
  empty,
  invalid,
  watched,
  path.join(ahead, "data"),
  path.join(locked, "data"),
])
  await mkdir(folder, { recursive: true });
await mkdir(first);
await writeFile(
  path.join(profile, "desktop-settings.json"),
  JSON.stringify({
    background: false,
    launchAtLogin: false,
    notifications: true,
    selectedBookFolder: await realpath(first),
    books: {},
  }),
);
await writeFile(path.join(invalid, "keep.txt"), "Unrelated folder.");
await mkdir("qa/artifacts", { recursive: true });
const runtime = process.env.ONW_DESKTOP_RUNTIME;
const python = runtime
  ? path.join(
      runtime,
      JSON.parse(await readFile(path.join(runtime, "manifest.json"), "utf8"))
        .python,
    )
  : path.join(repo, ".venv/bin/python3");
const backendCwd = runtime ?? repo;
function seed(root, script) {
  const result = spawnSync(python, ["-c", script], {
    cwd: backendCwd,
    env: { ...process.env, ONW_ROOT: root, ONW_QA_MODE: "1" },
    encoding: "utf8",
    timeout: 60000,
  });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim().split("\n").at(-1);
}
const account = JSON.parse(
  seed(
    second,
    `import json
from backend.db.migrate import ensure_migrated
ensure_migrated()
from backend.db.model_config_store import ModelConfigStore
ModelConfigStore().update({'provider':'openai_compatible','model':'desktop-qa','base_url':'http://127.0.0.1:9/v1'})
from backend.db.onboarding_state_store import OnboardingStateStore
OnboardingStateStore().mark_complete()
from backend.services.accounts import create_account
print(json.dumps(create_account({'name':'Second Book Cash','type':'cash','currency':'SGD','initial_balance':321,'initial_balance_date':'2026-10-01'})))`,
  ),
);
for (const root of [ahead, locked])
  await copyFile(
    path.join(second, "data/onw.db"),
    path.join(root, "data/onw.db"),
  );
seed(
  ahead,
  "import sqlite3,os; c=sqlite3.connect(os.path.join(os.environ['ONW_ROOT'],'data/onw.db')); c.execute(\"UPDATE alembic_version SET version_num='209901010000'\"); c.commit(); c.close()",
);
const digest = async (root) =>
  createHash("sha256")
    .update(await readFile(path.join(root, "data/onw.db")))
    .digest("hex");
const aheadBefore = await digest(ahead);
const env = {
  ...process.env,
  ONW_QA_MODE: "1",
  ONW_DESKTOP_REPO: repo,
  ONW_DESKTOP_PYTHON: python,
};
delete env.ONW_DESKTOP_ROOT;
let app, page, lockProcess;
const checks = [];
const errors = [];
const check = (name) => {
  checks.push(name);
  console.log("PASS", name);
};
async function api(url) {
  return page.evaluate(async (url) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: ${response.status}`);
    return response.json();
  }, url);
}
async function poll(action, timeout = 60000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    try {
      if (await action()) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("Book did not become ready");
}
async function assertRoot(root) {
  const canonical = await realpath(root);
  await poll(
    async () =>
      (await page.evaluate(() => window.onwDesktop.getSettings()))
        .bookFolder === canonical &&
      (await api("/api/setup-status/data-location")).instance_root ===
        canonical,
  );
}
async function launch() {
  app = await electron.launch({
    args: [process.cwd(), `--user-data-dir=${profile}`],
    env,
    timeout: 45000,
  });
  await app.evaluate(({BrowserWindow})=>{for(const window of BrowserWindow.getAllWindows())window.setPosition(-2500,-2500);});
  page = await app.firstWindow();
  page.setDefaultTimeout(20000);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.waitForURL(/127\.0\.0\.1/, { timeout: 60000 });
  await page
    .locator(".sidebar-item")
    .first()
    .waitFor({ state: "attached" }); // inert + aria-hidden behind first run, so wait for it to render
}
async function nativeChoice(folder, response = 0, cancelled = false) {
  await app.evaluate(
    ({ dialog }, options) => {
      globalThis.bookDialogs = [];
      dialog.showOpenDialog = async (_owner, config) => {
        globalThis.bookDialogs.push(config);
        return {
          canceled: options.cancelled,
          filePaths: options.folder ? [options.folder] : [],
        };
      };
      dialog.showMessageBox = async (_owner, config) => {
        globalThis.bookDialogs.push(config);
        return { response: options.response, checkboxChecked: false };
      };
    },
    { folder, response, cancelled },
  );
}
async function menu(label) {
  await app.evaluate(({ Menu }, label) => {
    const item = Menu.getApplicationMenu()
      .items.flatMap((item) => item.submenu?.items ?? [])
      .find((item) => item.label === label);
    if (!item) throw new Error(`Missing menu: ${label}`);
    item.click();
  }, label);
}
async function open(folder, response = 0) {
  await nativeChoice(folder, response);
  await menu("Open book folder…");
}
async function dialogs() {
  return app.evaluate(() => globalThis.bookDialogs);
}
async function stop() {
  if (!app) return;
  await app.evaluate(({ app }) => app.quit()).catch(() => {});
  await app.close().catch(() => {});
  app = null;
}
try {
  await launch();
  await assertRoot(first);
  await page.getByRole("heading", { name: "Choose your app currency." }).waitFor();
  assert.equal(await page.getByText("ONW_ROOT", { exact: true }).count(), 0);
  await nativeChoice(null, 0, true);
  await menu("Open book folder…");
  await poll(async () => (await dialogs()).length > 0);
  await assertRoot(first);
  await nativeChoice(empty, 1);
  await menu("Open book folder…");
  await poll(async () =>
    (await dialogs()).some((item) => item.message === "Create a book here?"),
  );
  await assertRoot(first);
  await assert.rejects(readFile(path.join(empty, "data/onw.db")), {
    code: "ENOENT",
  });
  await page.screenshot({ path: "qa/artifacts/book-01-setup-picker.png" });
  check(
    "File menu offers native picker; both cancellations preserve current book and leave empty folder untouched",
  );

  await nativeChoice(empty);
  await menu("Open book folder…");
  await assertRoot(empty);
  await page.waitForURL(/\/setup\/folder$/);
  await page.getByRole("button", { name: "Continue", exact: true }).waitFor();
  assert.equal((await page.evaluate(() => window.onwDesktop.getSettings())).bookFolder, await realpath(empty));
  assert.equal(await page.getByRole("button", { name: "Get started", exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: /backup to restore/i }).count(), 1); // the quiet restore link is part of step 01 now
  await page.screenshot({ path: "qa/artifacts/book-01b-setup-continues.png" });
  check("choosing an empty book during setup keeps the folder and currency step without repeating the greeting or restore choice");

  await nativeChoice(ahead);
  await menu("Open book folder…");
  await poll(async () => (await dialogs()).some((item) => item.type === "error"));
  await assertRoot(empty);
  await page.waitForURL(/\/setup\/folder$/);
  await page.getByRole("button", { name: "Continue", exact: true }).waitFor();
  assert.equal(await digest(ahead), aheadBefore);
  check("a refused book selection restores the previous fresh book at the folder and currency step");

  await nativeChoice(path.join(second, "data"));
  await menu("Open book folder…");
  await assertRoot(second);
  await page.getByRole("heading", { name: "Choose your app currency." }).waitFor({ state: "hidden" });
  assert.ok(!new URL(page.url()).pathname.startsWith("/setup"));
  assert.equal((await api(`/api/accounts/${account.id}`)).balance, 321);
  assert.equal((await api("/api/accounts")).length, 1);
  await readFile(path.join(first, "data/onw.db"));
  check(
    "choosing an existing data folder opens the complete book; exact balance survives and original book stays in place",
  );

  await menu("Settings…");
  await page.getByRole("button", { name: "Open another folder…" }).waitFor();
  assert.equal((await page.evaluate(() => window.onwDesktop.getSettings())).bookFolder, await realpath(second));
  await nativeChoice(watched);
  await page
    .getByRole("button", { name: "Choose folder…", exact: true })
    .click();
  await page.getByText("Watching", { exact: true }).waitFor();
  const scopedFolder = (
    await page.evaluate(() => window.onwDesktop.getSettings())
  ).watchedFolders[0];
  await page.screenshot({ path: "qa/artifacts/book-02-settings.png" });
  check(
    "Desktop settings shows canonical book path and keeps statement access scoped to that book",
  );

  await nativeChoice(empty);
  await page.getByRole("button", { name: "Open another folder…" }).click();
  await assertRoot(empty);
  assert.deepEqual(await api("/api/accounts"), []);
  assert.equal(
    (await page.evaluate(() => window.onwDesktop.getSettings())).watchedFolders
      .length,
    0,
  );
  await page.getByRole("heading", { name: "Choose your app currency." }).waitFor();
  await page.screenshot({ path: "qa/artifacts/book-03-new-book.png" });
  check(
    "confirmed empty folder creates a separate new book and fresh setup without carrying financial rows or statement grants",
  );

  await open(second);
  await assertRoot(second);
  assert.equal((await api(`/api/accounts/${account.id}`)).balance, 321);
  assert.equal(
    (await page.evaluate(() => window.onwDesktop.getSettings()))
      .watchedFolders[0].id,
    scopedFolder.id,
  );
  check(
    "File menu returns to existing book and restores its own statement grants",
  );
  await stop();
  await launch();
  await assertRoot(second);
  assert.equal((await api(`/api/accounts/${account.id}`)).balance, 321);
  check(
    "quit and relaunch remember selected book without changing the global server pointer",
  );

  await open(invalid);
  await poll(async () => (await readdir(invalid)).includes("Open Net Worth"));
  await assertRoot(path.join(invalid, "Open Net Worth"));
  await open(second);
  await assertRoot(second);
  assert.equal(
    await readFile(path.join(invalid, "keep.txt"), "utf8"),
    "Unrelated folder.",
  );
  await assert.rejects(readFile(path.join(invalid, "data/onw.db")), {
    code: "ENOENT",
  });
  check(
    "a non-book folder with other files gets its book in an Open Net Worth subfolder, without initializing or altering the folder itself",
  );

  await menu("Settings…");
  await page.getByRole("button", { name: "Open another folder…" }).waitFor();
  await nativeChoice(ahead);
  await page.getByRole("button", { name: "Open another folder…" }).click();
  await poll(async () =>
    (await dialogs()).some((item) => item.type === "error"),
  );
  await assertRoot(second);
  assert.equal(await digest(ahead), aheadBefore);
  assert.equal((await api(`/api/accounts/${account.id}`)).balance, 321);
  check(
    "newer schema is refused untouched and previous book restarts automatically",
  );

  lockProcess = spawn(
    python,
    [
      "-c",
      'import sys; from pathlib import Path; from backend.core.instance_lock import acquire; acquire(Path(sys.argv[1]),8097,instance_id="book-picker-lock-fixture"); print("LOCKED",flush=True); sys.stdin.read() ',
      locked,
    ],
    {
      cwd: backendCwd,
      env: { ...process.env, ONW_ROOT: locked, ONW_QA_MODE: "1" },
      stdio: ["pipe", "pipe", "pipe"],
    },
  );
  await new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("Lock fixture timed out")),
      15000,
    );
    lockProcess.stdout.once("data", () => {
      clearTimeout(timer);
      resolve();
    });
    lockProcess.once("exit", () => {
      clearTimeout(timer);
      reject(new Error("Lock fixture exited early"));
    });
  });
  const lockedBefore = await digest(locked);
  await open(locked);
  await poll(async () =>
    (await dialogs()).some((item) => item.type === "error"),
  );
  await assertRoot(second);
  assert.equal(await digest(locked), lockedBefore);
  assert.ok(
    (await dialogs()).some(
      (item) =>
        item.type === "error" &&
        /port 8097.*Stop that server and retry/.test(item.message),
    ),
  );
  check(
    "a book owned by another process is refused untouched and previous book stays usable",
  );
  await stop();
  await launch();
  await assertRoot(second);
  assert.equal((await api(`/api/accounts/${account.id}`)).balance, 321);
  assert.deepEqual(errors, []);
  await stop();
  const prefsPath = path.join(profile, "desktop-settings.json");
  const saved = JSON.parse(await readFile(prefsPath, "utf8"));
  saved.selectedBookFolder = path.join(fixture, "Missing Book");
  await writeFile(prefsPath, JSON.stringify(saved));
  app = await electron.launch({
    args: [process.cwd(), `--user-data-dir=${profile}`],
    env,
    timeout: 45000,
  });
  await app.evaluate(({BrowserWindow})=>{for(const window of BrowserWindow.getAllWindows())window.setPosition(-2500,-2500);});
  page = await app.firstWindow();
  await page
    .getByRole("button", { name: "Open another folder…", exact: true })
    .waitFor({ timeout: 60000 });
  await nativeChoice(second);
  await page
    .getByRole("button", { name: "Open another folder…", exact: true })
    .click();
  await assertRoot(second);
  assert.equal((await api(`/api/accounts/${account.id}`)).balance, 321);
  check(
    "an unavailable remembered book has a native recovery picker that opens another book",
  );
  await writeFile(
    "qa/artifacts/book-picker-report.json",
    JSON.stringify(
      {
        checks,
        fixture,
        errors,
        packaged: false,
        modelEnrollment: "fixture; no paid model calls",
      },
      null,
      2,
    ),
  );
  console.log("BOOK_PICKER_E2E_OK", checks.length, "checks");
} catch (error) {
  await page
    ?.screenshot({ path: "qa/artifacts/book-picker-failure.png" })
    .catch(() => {});
  console.error("BOOK_PICKER_E2E_FAILED", error);
  process.exitCode = 1;
} finally {
  lockProcess?.stdin.end();
  await stop();
}
