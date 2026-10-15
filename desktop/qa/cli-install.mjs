import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { _electron as electron, chromium } from "playwright";

const repo = path.resolve("..");
const fixture = await mkdtemp(path.join(tmpdir(), "onw-cli-install-"));
const book = path.join(fixture, "Book");
const cliHome = path.join(fixture, "Home");
const claude = path.join(cliHome, ".local/bin/claude");
const codex = path.join(cliHome, ".local/bin/codex");
const bundledPython = JSON.parse(
  await readFile("runtime/manifest.json", "utf8"),
).python;
for (const directory of [book, path.dirname(claude), "qa/artifacts"])
  await mkdir(directory, { recursive: true });
const env = {
  ...process.env,
  HOME: cliHome,
  PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
  ONW_QA_MODE: "1",
  ONW_DESKTOP_ROOT: book,
  ONW_DESKTOP_REPO: repo,
  ONW_DESKTOP_PYTHON: path.resolve("runtime", bundledPython),
};
delete env.ONW_DESKTOP_RUNTIME;
const checks = [];
const errors = [];
const check = (name) => {
  checks.push(name);
  console.log("PASS", name);
};
const fakeBinary =
  '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "2.1.287 (Synthetic QA CLI)"; exit 0; fi\necho "No inference in installation QA" >&2\nexit 1\n';
const app = await electron.launch({
  args: [process.cwd()],
  env,
  timeout: 45000,
});
let browser;
let qaPage;
async function api(
  page,
  url,
  body,
  method = body === undefined ? "GET" : "POST",
) {
  return page.evaluate(
    async ({ url, body, method }) => {
      const response = await fetch(url, {
        method,
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok) throw new Error(`${url}: ${response.status}`);
      return response.json();
    },
    { url, body, method },
  );
}
try {
  await app.evaluate(async ({ clipboard, ClipboardItem }) => {
    const snapshot = [];
    for (const item of await clipboard.read()) {
      const data = {};
      for (const type of item.types) data[type] = await item.getType(type);
      snapshot.push(new ClipboardItem(data));
    }
    globalThis.cliInstallClipboardBackup = snapshot;
  });
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows().forEach((window) =>
      window.setPosition(-2500, -2500),
    ),
  );
  const page = await app.firstWindow();
  qaPage = page;
  page.setDefaultTimeout(20000);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.waitForURL(/127\.0\.0\.1/, { timeout: 60000 });
  const origin = new URL(page.url()).origin;
  await page
    .getByRole("heading", { name: "Choose your app currency." })
    .waitFor();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByLabel("API key", { exact: true }).waitFor();
  const catalog = await api(page, "/api/model-config/providers");
  assert.equal(catalog.cli_host_platform, "macos");
  assert.equal(catalog.detected_clis.claude_cli, false);
  assert.equal(catalog.detected_clis.codex_cli, false);
  await page.getByText("Claude Code", { exact: true }).waitFor();
  await page.getByText("Codex", { exact: true }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "Use this", exact: true }).count(),
    0,
  );
  await page.screenshot({
    path: "qa/artifacts/cli-install-desktop-choices.png",
  });
  check(
    "desktop offers both subscription choices before either CLI is installed",
  );

  const claudeHelp = page
    .locator(".firstrun-found")
    .filter({ has: page.getByText("Claude Code", { exact: true }) });
  await claudeHelp.getByRole("button", { name: "Install & sign in" }).click();
  await claudeHelp
    .getByRole("button", { name: "Copy Claude Code install command" })
    .click();
  await claudeHelp
    .getByRole("button", { name: "Copy Claude Code install command" })
    .getByText("Copied", { exact: true })
    .waitFor();
  assert.equal(
    (await app.evaluate(({ clipboard }) => clipboard.readText())) ===
      "curl -fsSL https://claude.ai/install.sh | bash",
    true,
    "Claude installation clipboard mismatch",
  );
  await claudeHelp
    .getByRole("button", { name: "Copy Claude Code sign-in command" })
    .click();
  await claudeHelp
    .getByRole("button", { name: "Copy Claude Code sign-in command" })
    .getByText("Copied", { exact: true })
    .waitFor();
  assert.equal(
    (await app.evaluate(({ clipboard }) => clipboard.readText())) ===
      "claude auth login",
    true,
    "Claude sign-in clipboard mismatch",
  );
  await page.screenshot({
    path: "qa/artifacts/cli-install-desktop-instructions.png",
  });
  check(
    "desktop copies exact official install and subscription sign-in commands",
  );

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    permissions: ["clipboard-read", "clipboard-write"],
  });
  const browserPage = await context.newPage();
  browserPage.on("pageerror", (error) => errors.push(error.message));
  await browserPage.goto(`${origin}/setup/model`);
  await browserPage
    .getByLabel("API key", { exact: true })
    .fill("synthetic-browser-draft");
  const codexHelp = browserPage
    .locator(".firstrun-found")
    .filter({ has: browserPage.getByText("Codex", { exact: true }) });
  await codexHelp.getByRole("button", { name: "Install & sign in" }).click();
  await codexHelp
    .getByText(/Run these commands on the machine running Open Net Worth/)
    .waitFor();
  await codexHelp
    .getByRole("button", { name: "Copy Codex install command" })
    .click();
  await codexHelp
    .getByRole("button", { name: "Copy Codex install command" })
    .getByText("Copied", { exact: true })
    .waitFor();
  assert.equal(
    (await browserPage.evaluate(() => navigator.clipboard.readText())) ===
      "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
    true,
    "Codex installation clipboard mismatch",
  );
  await codexHelp
    .getByRole("button", { name: "Copy Codex sign-in command" })
    .click();
  await codexHelp
    .getByRole("button", { name: "Copy Codex sign-in command" })
    .getByText("Copied", { exact: true })
    .waitFor();
  assert.equal(
    (await browserPage.evaluate(() => navigator.clipboard.readText())) ===
      "codex login",
    true,
    "Codex sign-in clipboard mismatch",
  );
  await browserPage.screenshot({
    path: "qa/artifacts/cli-install-browser-instructions.png",
  });
  check(
    "ordinary browser explains the server machine and copies Codex commands without a native bridge",
  );

  await page
    .getByLabel("API key", { exact: true })
    .fill("synthetic-desktop-draft");
  const mutations = [];
  for (const surface of [page, browserPage])
    surface.on("request", (request) => {
      if (
        request.url().includes("/api/model-config") &&
        request.method() !== "GET"
      )
        mutations.push(request.method() + " " + request.url());
    });
  await writeFile(claude, fakeBinary, { mode: 0o700 });
  await writeFile(codex, fakeBinary, { mode: 0o700 });
  await claudeHelp
    .getByRole("button", { name: "Use this", exact: true })
    .waitFor();
  await codexHelp
    .getByRole("button", { name: "Use this", exact: true })
    .waitFor();
  assert.equal(
    await page.getByLabel("API key", { exact: true }).inputValue(),
    "synthetic-desktop-draft",
  );
  assert.equal(
    await browserPage.getByLabel("API key", { exact: true }).inputValue(),
    "synthetic-browser-draft",
  );
  assert.equal(
    await claudeHelp
      .getByRole("button", { name: "Install & sign in" })
      .getAttribute("aria-expanded"),
    "true",
  );
  assert.equal(
    await codexHelp
      .getByRole("button", { name: "Install & sign in" })
      .getAttribute("aria-expanded"),
    "true",
  );
  assert.deepEqual(mutations, []);
  check(
    "real executable discovery updates desktop and browser automatically without choosing a provider or resetting drafts",
  );

  await browserPage.setViewportSize({ width: 320, height: 780 });
  assert.equal(
    await browserPage.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  await browserPage.screenshot({
    path: "qa/artifacts/cli-install-browser-narrow.png",
    fullPage: true,
  });
  check(
    "expanded install instructions fit a 320-pixel browser without horizontal overflow",
  );
  await claudeHelp
    .getByRole("button", { name: "Use this", exact: true })
    .click();
  await page.getByLabel("Model", { exact: true }).waitFor();
  assert.ok(mutations.some((entry) => entry.includes("/claude_cli/validate")));
  check(
    "explicit Use this runs real version validation and advances to model selection",
  );
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "Skip", exact: true }).waitFor();

  await rm(codex);
  await api(
    page,
    "/api/model-config",
    {
      provider: "openai_compatible",
      model: "installation-qa",
      base_url: "http://127.0.0.1:9/v1",
    },
    "PUT",
  );
  await page.getByRole("button", { name: "Skip", exact: true }).click();
  await page.waitForURL(/\/lucius/);
  await page.goto(`${origin}/settings?sub=model`);
  const settingsHelp = page
    .locator(".rd-prov")
    .filter({ has: page.getByText("Codex CLI", { exact: true }) });
  await settingsHelp.getByRole("button", { name: "Install & sign in" }).click();
  await settingsHelp
    .getByRole("button", { name: "Copy Codex install command" })
    .waitFor();
  await page.screenshot({ path: "qa/artifacts/cli-install-settings.png" });
  await writeFile(codex, fakeBinary, { mode: 0o700 });
  await settingsHelp.getByText("Installed", { exact: true }).waitFor();
  assert.equal(await page.getByText("Codex CLI", { exact: true }).count(), 1);
  assert.equal(
    await settingsHelp
      .getByRole("button", { name: "Install & sign in" })
      .getAttribute("aria-expanded"),
    "true",
  );
  await settingsHelp
    .getByRole("button", { name: "Copy Codex sign-in command" })
    .waitFor();
  check(
    "AI settings offers missing subscriptions and promotes a newly installed CLI into its provider list",
  );
  assert.deepEqual(errors, []);
  await writeFile(
    "qa/artifacts/cli-install-report.json",
    JSON.stringify({ fixture, checks }, null, 2),
  );
} catch (error) {
  await qaPage
    ?.screenshot({ path: "qa/artifacts/cli-install-failure.png" })
    .catch(() => {});
  console.log("QA failure view:", qaPage?.url());
  throw error;
} finally {
  await browser?.close();
  await app
    .evaluate(async ({ clipboard }) => {
      if (
        ![
          "claude auth login",
          "curl -fsSL https://claude.ai/install.sh | bash",
          "codex login",
          "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
        ].includes(await clipboard.readText())
      )
        return;
      if (globalThis.cliInstallClipboardBackup?.length)
        await clipboard.write(globalThis.cliInstallClipboardBackup);
      else clipboard.clear();
    })
    .catch(() => {});
  await app.close();
}
