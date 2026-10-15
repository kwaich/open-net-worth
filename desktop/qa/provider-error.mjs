import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { _electron as electron } from "playwright";

const repo = path.resolve("..");
const root = await mkdtemp(path.join(tmpdir(), "onw-provider-error-"));
const python = path.join(repo, ".venv/bin/python3");
const missing = path.join(root, "missing-codex");
const seed = spawnSync(
  python,
  [
    "-c",
    `
from backend.db.migrate import ensure_migrated
ensure_migrated()
from backend.db.model_config_store import ModelConfigStore
ModelConfigStore().update({'provider':'codex_cli','model':'gpt-6.1-sol','base_url':${JSON.stringify(missing)}})
from backend.db.onboarding_state_store import OnboardingStateStore
OnboardingStateStore().mark_complete()
from backend.services.accounts import create_account
create_account({'name':'Error QA','type':'cash','currency':'SGD','initial_balance':0,'initial_balance_date':'2026-10-01'})
`,
  ],
  {
    cwd: repo,
    env: { ...process.env, ONW_ROOT: root, ONW_QA_MODE: "1" },
    encoding: "utf8",
    timeout: 60000,
  },
);
assert.equal(seed.status, 0, seed.stderr);
const app = await electron.launch({
  args: [process.cwd()],
  env: {
    ...process.env,
    ONW_ROOT: root,
    ONW_QA_MODE: "1",
    ONW_DESKTOP_ROOT: root,
    ONW_DESKTOP_REPO: repo,
    ONW_DESKTOP_PYTHON: python,
  },
  timeout: 45000,
});
const checks = [];
try {
  await app.evaluate(({ BrowserWindow }) => {
    for (const window of BrowserWindow.getAllWindows())
      window.setPosition(-2500, -2500);
  });
  const page = await app.firstWindow();
  page.setDefaultTimeout(20000);
  await page.waitForURL(/127\.0\.0\.1/, { timeout: 60000 });
  // /lucius opens on a fresh conversation (LuciusHome's startFresh), so "New" is a no-op here:
  // wait for that conversation's history fetch instead of clicking it.
  const historyLoaded = page.waitForResponse((response) =>
    /\/api\/sessions\/[^/]+\/messages/.test(response.url()),
  );
  await page.goto(`${new URL(page.url()).origin}/lucius`);
  await historyLoaded;
  await new Promise((resolve) => setTimeout(resolve, 1500));
  const composer = page.locator('[contenteditable="true"]:visible').first();
  await composer.waitFor();
  await composer.fill("hello from the provider failure test");
  await composer.press("Enter");
  const alert = page
    .getByRole("alert")
    .filter({ hasText: "Codex CLI not found" });
  await alert.waitFor();
  console.log("PASS immediate Codex failure");
  assert.ok((await alert.innerText()).includes(missing));
  checks.push("missing Codex is visible immediately without assistant output");
  await alert.getByRole("button", { name: "Open AI settings" }).click();
  await page.waitForURL(/\/settings\?sub=model/);
  await page.getByText("Codex CLI", { exact: true }).waitFor();
  checks.push("recovery action opens the AI provider settings");
  await page.goBack();
  await alert.waitFor();
  await page.reload();
  await alert.waitFor();
  checks.push("failure remains visible after reload");
  await mkdir("qa/artifacts", { recursive: true });
  await page.screenshot({ path: "qa/artifacts/provider-error.png" });
  const response = await page.evaluate(async () => {
    const r = await fetch("/api/model-config", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({
        provider: "claude_cli",
        model: "sonnet",
        base_url: "/missing/claude-error-qa",
      }),
    });
    return r.status;
  });
  assert.equal(response, 200);
  await page.reload();
  await composer.waitFor();
  await composer.fill("hello from the Claude failure test");
  await composer.press("Enter");
  await page
    .getByRole("alert")
    .filter({ hasText: "Claude Code CLI not found" })
    .waitFor();
  checks.push("missing Claude is visible on a subsequent turn");
  await writeFile(
    "qa/artifacts/provider-error-report.json",
    JSON.stringify({ checks, root }, null, 2),
  );
  for (const check of checks) console.log("PASS", check);
} catch (error) {
  const page = await app.firstWindow();
  console.log(
    "QA page:",
    page.url(),
    (await page.locator("body").innerText()).slice(-2500),
  );
  await page.screenshot({ path: "qa/artifacts/provider-error-failed.png" });
  throw error;
} finally {
  await app.evaluate(({ app }) => app.quit()).catch(() => {});
  await app.close().catch(() => {});
}
