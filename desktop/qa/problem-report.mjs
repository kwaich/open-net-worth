import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { _electron as electron } from "playwright";

// "Report a problem" on the screens that have no book behind them: the Welcome page, the page that
// follows a failed start, and the app itself. Every report goes to a local receiver, never to the
// real endpoint, and each must arrive byte-for-byte as the preview showed it.
const APP_VERSION = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")).version;
const repo = path.resolve("..");
const goodPython = path.join(repo, ".venv/bin/python3");
const fixture = await mkdtemp(path.join(tmpdir(), "onw-problem-report-"));
await mkdir("qa/artifacts", { recursive: true });

const received = [];
const receiver = createServer((request, response) => {
  let body = "";
  request.on("data", (chunk) => { body += chunk; });
  request.on("end", () => {
    received.push(body);
    response.writeHead(201, { "Content-Type": "application/json" });
    response.end('{"id":"rep_qa_desktop"}');
  });
});
await new Promise((resolve) => receiver.listen(0, "127.0.0.1", resolve));
const endpoint = `http://127.0.0.1:${receiver.address().port}/api/problem-reports`;

const checks = [];
const check = (name) => { checks.push(name); console.log("PASS", name); };

async function launch(name, python) {
  const home = path.join(fixture, name, "home");
  const profile = path.join(fixture, name, "profile");
  await mkdir(home, { recursive: true });
  await mkdir(profile, { recursive: true });
  const entry = path.join(fixture, name, "entry.cjs");
  await writeFile(path.join(fixture, name, "package.json"), JSON.stringify({ name: `onw-problem-report-${name}`, version: APP_VERSION, main: "entry.cjs" }));
  await writeFile(entry, `const { app } = require('electron');\napp.setPath('home', ${JSON.stringify(home)});\napp.setPath('userData', ${JSON.stringify(profile)});\nrequire(${JSON.stringify(path.join(process.cwd(), "dist", "main", "index.js"))});\n`);
  const env = { ...process.env, ONW_QA_MODE: "1", ONW_DESKTOP_REPO: repo, ONW_DESKTOP_PYTHON: python, ONW_REPORTS_URL: endpoint };
  delete env.ONW_DESKTOP_ROOT;
  const app = await electron.launch({ args: [path.join(fixture, name)], env, timeout: 45000 });
  await app.evaluate(({ app: electronApp, BrowserWindow }) => {
    const hide = (window) => window.setPosition(-2500, -2500);
    BrowserWindow.getAllWindows().forEach(hide);
    electronApp.on("browser-window-created", (_event, window) => hide(window));
  });
  const page = await app.firstWindow();
  page.setDefaultTimeout(30000);
  return { app, page };
}

async function openFromMenu(app) {
  await app.evaluate(({ Menu }) => {
    const help = Menu.getApplicationMenu().items.find((item) => item.label === "Help");
    help.submenu.items.find((item) => item.label === "Report a Problem…").click();
  });
}

async function dialog(app) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    for (const candidate of app.windows()) {
      if (!candidate.isClosed() && (await candidate.title().catch(() => "")).startsWith("Report a problem")) return candidate;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("The report dialog did not open.");
}

// Writes a description, sends, and returns [what the preview showed, what the receiver got].
async function sendFrom(app, window, description, shot) {
  await window.locator("#description").fill(description);
  const previewed = JSON.parse(await window.locator("#json").textContent());
  if (shot) {
    // Tall enough to show the whole report in one picture; the real window scrolls.
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().find((candidate) => candidate.getTitle().startsWith("Report a problem"))?.setSize(720, 1180));
    await window.screenshot({ path: `qa/artifacts/${shot}.png` });
  }
  const before = received.length;
  await window.getByRole("button", { name: "Send report" }).click();
  await window.locator("#done").waitFor({ state: "visible" });
  assert.equal(received.length, before + 1, "exactly one report was sent");
  const sent = JSON.parse(received.at(-1));
  assert.deepEqual(sent, previewed, "the preview is exactly what was sent");
  assert.equal(received.at(-1), JSON.stringify(previewed));
  await window.locator("#close").click();
  return sent;
}

let welcome;
let broken;
try {
  // 1. Welcome, with no book open: the menu item works and reports where the user is.
  welcome = await launch("welcome", goodPython);
  await welcome.page.getByRole("button", { name: "Get started" }).waitFor();
  assert.equal(received.length, 0, "nothing is sent just by opening the app");
  await openFromMenu(welcome.app);
  let window = await dialog(welcome.app);
  let sent = await sendFrom(welcome.app, window, "Reporting from the Welcome page.", "problem-report-desktop-welcome");
  assert.equal(sent.screen, "welcome");
  assert.equal(sent.mode, "desktop");
  assert.equal(sent.app_version, APP_VERSION);
  assert.equal(sent.error, null);
  assert.equal(JSON.stringify(sent).includes(fixture), false, "no temp folder path leaks");
  check("Help > Report a Problem works on the Welcome page, with no book open");

  // 2. The same app once its book is running: the screen is the app's route, and its log is attached.
  await welcome.page.getByRole("button", { name: "Get started" }).click();
  await welcome.page.getByRole("button", { name: "Continue" }).click();
  await welcome.page.waitForURL(/127\.0\.0\.1/, { timeout: 90000 });
  await openFromMenu(welcome.app);
  window = await dialog(welcome.app);
  sent = await sendFrom(welcome.app, window, "Reporting from inside the app.", "problem-report-desktop");
  assert.match(sent.screen, /^\/setup/);
  assert.match(sent.logs, /^# book log\n/);
  assert.equal(sent.logs.includes(fixture), false, "the book folder is masked in the logs");
  check("Help > Report a Problem works inside the app and attaches the redacted book log");
  assert.equal(received.length, 2);
  check("only the two reports the user sent were ever sent");
} finally {
  await welcome?.app.close().catch(() => {});
}

try {
  // 3. A start that fails: the recovery page's own button opens the report with its error filled in.
  broken = await launch("broken", path.join(fixture, "no-such-python"));
  await broken.page.getByRole("button", { name: "Get started" }).click();
  await broken.page.getByRole("button", { name: "Continue" }).click();
  await broken.page.getByRole("button", { name: "Report this problem" }).waitFor({ timeout: 60000 });
  await broken.page.getByRole("button", { name: "Report this problem" }).click();
  const window = await dialog(broken.app);
  const sent = await sendFrom(broken.app, window, "It could not start.", "problem-report-desktop-recovery");
  assert.equal(sent.screen, "recovery: could not start");
  assert.ok(sent.error && sent.error.length > 0, "the failure shown on the page is prefilled");
  assert.equal(JSON.stringify(sent).includes(fixture), false);
  check("the recovery page's Report this problem opens a report prefilled with its error");
} finally {
  await broken?.app.close().catch(() => {});
  receiver.close();
}
console.log(`${checks.length} checks passed`);
