import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { _electron as electron } from "playwright";

const repo = path.resolve("..");
const root = await mkdtemp(path.join(tmpdir(), "onw-splash-"));
const wrapper = path.join(root, "delayed-python");
const python = path.join(repo, ".venv/bin/python3");
const quote = (value) => `'${value.replaceAll("'", "'\\''")}'`;
await writeFile(wrapper, `#!/bin/sh\nsleep 8\nexec ${quote(python)} "$@"\n`, {
  mode: 0o700,
});
await mkdir("qa/artifacts", { recursive: true });
const app = await electron.launch({
  args: [process.cwd()],
  env: {
    ...process.env,
    ONW_QA_MODE: "1",
    ONW_DESKTOP_ROOT: root,
    ONW_DESKTOP_REPO: repo,
    ONW_DESKTOP_PYTHON: wrapper,
  },
  timeout: 45000,
});
const checks = [];
const sourceLogo = await readFile(
  path.join(repo, "frontend/public/logo-mark.svg"),
  "utf8",
);
const markPath = sourceLogo.match(/<path[^>]* d="([^"]+)"/)[1];
try {
  const trayIcon = await app.evaluate(({ app, nativeImage }) => {
    const icon = nativeImage.createFromPath(
      `${app.getAppPath()}/resources/trayTemplate.png`,
    );
    return {
      empty: icon.isEmpty(),
      size: icon.getSize(),
      scales: icon.getScaleFactors(),
    };
  });
  assert.equal(trayIcon.empty, false);
  assert.deepEqual(trayIcon.size, { width: 20, height: 20 });
  assert.ok(trayIcon.scales.includes(2));
  checks.push(
    "menu-bar mark icon loads with both standard and Retina representations",
  );
  await app.evaluate(({ BrowserWindow }) => {
    for (const window of BrowserWindow.getAllWindows())
      window.setPosition(-2500, -2500);
  });
  const page = await app.firstWindow();
  page.setDefaultTimeout(20000);
  await page.getByRole("status", { name: "Opening your book…" }).waitFor();
  const systemAppearance = await app.evaluate(({nativeTheme})=>({ source:nativeTheme.themeSource,dark:nativeTheme.shouldUseDarkColors }));
  assert.equal(systemAppearance.source,'system');
  assert.equal(await page.locator('html').getAttribute('data-theme'),systemAppearance.dark?'dark':'light');
  assert.equal(await page.locator(".brand-logo rect").count(), 0);
  assert.equal(await page.getByRole("heading").count(), 0);
  const splash = async () =>
    page.evaluate(async () => {
      await document.fonts.ready;
      const body = getComputedStyle(document.body);
      const loading = getComputedStyle(document.querySelector(".loading"));
      const main = document.querySelector("main").getBoundingClientRect();
      return {
        font: body.fontFamily,
        size: loading.fontSize,
        color: loading.color,
        background: body.backgroundColor,
        fontsLoaded: [...document.fonts].some(
          (font) => font.weight === "400" && font.status === "loaded",
        ),
        centered: Math.abs(main.x + main.width / 2 - innerWidth / 2) < 2,
        animation: getComputedStyle(document.querySelector(".spinner"))
          .animationName,
        logoSize: document.querySelector(".brand-logo").getBoundingClientRect()
          .width,
        logoFilter: getComputedStyle(document.querySelector(".brand-logo"))
          .filter,
        markPath: document.querySelector(".brand-logo path").getAttribute("d"),
      };
    });
  for (const [scheme, width, background, color] of [
    ["dark", 1024, "rgb(14, 16, 20)", "rgb(154, 163, 178)"],
    ["light", 1440, "rgb(255, 255, 255)", "rgb(101, 105, 112)"],
  ]) {
    await app.evaluate(
      ({ BrowserWindow }, width) =>
        BrowserWindow.getAllWindows()[0].setSize(width, 880),
      width,
    );
    await app.evaluate(({nativeTheme},scheme)=>{nativeTheme.themeSource=scheme;},scheme);
    await page.locator(`html[data-theme=${scheme}]`).waitFor();
    await page.emulateMedia({ colorScheme: scheme });
    const style = await splash();
    assert.equal(style.background, background);
    assert.equal(style.color, color);
    assert.equal(style.size, "14px");
    assert.ok(style.font.includes("Plus Jakarta Sans"));
    assert.ok(style.fontsLoaded);
    assert.ok(style.centered);
    assert.equal(style.logoSize, 64);
    assert.equal(style.logoFilter, scheme === "light" ? "invert(1)" : "none");
    assert.equal(style.markPath, markPath);
    await page.screenshot({ path: `qa/artifacts/splash-${scheme}.png` });
    checks.push(
      `${scheme} splash uses the app's logo mark and matches onboarding at ${width}px`,
    );
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  assert.equal((await splash()).animation, "none");
  checks.push("reduced motion keeps a static loading indicator");
  await page.waitForURL(/127\.0\.0\.1/, { timeout: 60000 });
  await page.getByRole("heading", { name: "Choose your app currency." }).waitFor();
  const onboarding = await page.evaluate(() => {
    const body = getComputedStyle(document.body);
    return {
      font: body.fontFamily,
      size: body.fontSize,
      background: body.backgroundColor,
    };
  });
  assert.ok(onboarding.font.includes("Plus Jakarta Sans"));
  assert.equal(onboarding.size, "14px");
  assert.equal(onboarding.background, "rgb(255, 255, 255)");
  checks.push(
    "startup transitions to onboarding with matching type and background",
  );
  const owner = JSON.parse(
    await readFile(path.join(root, ".instance.lock"), "utf8"),
  );
  process.kill(owner.pid, "SIGTERM");
  await page
    .getByRole("heading", { name: "Open Net Worth could not start" })
    .waitFor();
  for (const name of ["Try again", "Choose another folder", "Open logs folder"]) {
    await page.getByRole("button", { name, exact: true }).waitFor();
  }
  const recovery = await page.evaluate(async () => {
    await document.fonts.ready;
    const heading = getComputedStyle(document.querySelector("h1"));
    return {
      size: heading.fontSize,
      weight: heading.fontWeight,
      body: getComputedStyle(document.body).fontSize,
    };
  });
  assert.deepEqual(recovery, { size: "44px", weight: "400", body: "14px" });
  await page.screenshot({ path: "qa/artifacts/splash-recovery.png" });
  await page
    .getByRole("button", { name: "Try again", exact: true })
    .click();
  await page.getByRole("status", { name: "Opening your book…" }).waitFor();
  await page.waitForURL(/127\.0\.0\.1/, { timeout: 60000 });
  await page.getByRole("heading", { name: "Choose your app currency." }).waitFor();
  checks.push(
    "branded failure actions remain usable and Retry returns through the splash",
  );
  await writeFile(
    "qa/artifacts/splash-report.json",
    JSON.stringify({ checks, root }, null, 2),
  );
  for (const check of checks) console.log("PASS", check);
} finally {
  await app.evaluate(({ app }) => app.quit()).catch(() => {});
  await app.close().catch(() => {});
}
