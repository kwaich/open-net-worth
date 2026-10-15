// Capture deterministic visual evidence for the transaction-review entry point and card.
// Run only against an isolated QA instance on ports 8090-8099.

import { chromium } from "../../frontend/node_modules/playwright/index.mjs";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.join(here, "artifacts", "transaction-review-mode");
const appUrl = process.env.APP_URL || "http://localhost:8097";
const parsed = new URL(appUrl);
if (!["localhost", "127.0.0.1"].includes(parsed.hostname)) {
  throw new Error("APP_URL must be local");
}
if (Number(parsed.port) < 8090 || Number(parsed.port) > 8099) {
  throw new Error("APP_URL must use a QA port from 8090 through 8099");
}
await mkdir(out, { recursive: true });

function check(condition, message) {
  if (!condition) throw new Error(message);
}

async function capture(browser, name, viewport) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await page.goto(`${appUrl}/accounts`, { waitUntil: "networkidle" });
  const backlog = page.getByRole("button", { name: /\d+ to review/ });
  await backlog.waitFor({ state: "visible", timeout: 20_000 });
  await page.screenshot({
    path: path.join(out, `${name}-lucius-entry.png`),
    fullPage: true,
  });

  await backlog.click();
  const dialog = page.getByRole("dialog", { name: "Review transactions" });
  await dialog.waitFor({ state: "visible", timeout: 20_000 });
  await page
    .locator(".acct-review-card")
    .waitFor({ state: "visible", timeout: 20_000 });
  await page.waitForTimeout(300);
  const overlayRight = await page
    .locator(".acct-review-overlay")
    .evaluate((overlay) => {
      const rect = overlay.getBoundingClientRect();
      return Math.round(innerWidth - rect.right);
    });
  check(
    overlayRight === 0,
    `${name}: review backdrop leaves a ${overlayRight}px right gap`,
  );
  const geometry = await page.evaluate(() => {
    const dialogRect = document
      .querySelector(".acct-review-mode")
      ?.getBoundingClientRect();
    const cardRect = document
      .querySelector(".acct-review-card")
      ?.getBoundingClientRect();
    const actionsRect = document
      .querySelector(".acct-review-actions")
      ?.getBoundingClientRect();
    const fields = document.querySelector(".acct-review-fields-scroll");
    const amount = document
      .querySelector(".acct-review-summary-amount strong")
      ?.getBoundingClientRect();
    const currency = document
      .querySelector(".acct-review-summary-amount em")
      ?.getBoundingClientRect();
    return {
      dialogTop: dialogRect?.top ?? -1,
      dialogBottom: dialogRect?.bottom ?? -1,
      cardTop: cardRect?.top ?? -1,
      cardBottom: cardRect?.bottom ?? -1,
      actionsBottom: actionsRect?.bottom ?? -1,
      hasEditorFields: Boolean(fields),
      fieldsScrollable: fields
        ? fields.scrollHeight > fields.clientHeight
        : false,
      amountCurrencyOverlap: Boolean(
        amount && currency && amount.right > currency.left,
      ),
    };
  });
  check(
    geometry.cardTop >= 0,
    `${name}: review card starts above the viewport`,
  );
  check(
    geometry.cardBottom <= viewport.height,
    `${name}: review card is cut off below the viewport`,
  );
  check(
    geometry.actionsBottom <= viewport.height,
    `${name}: action row is cut off below the viewport`,
  );
  check(
    !geometry.amountCurrencyOverlap,
    `${name}: amount overlaps its currency`,
  );
  if (name === "mobile") {
    check(
      Math.abs(geometry.dialogTop) <= 1,
      "mobile: review surface does not start at the top of the viewport",
    );
    check(
      Math.abs(geometry.dialogBottom - viewport.height) <= 1,
      "mobile: review surface does not fill the viewport",
    );
    check(
      !geometry.hasEditorFields,
      "mobile: review mode exposed editing fields",
    );
  }
  await page.screenshot({
    path: path.join(out, `${name}-review-card.png`),
    fullPage: true,
  });

  if (name === "desktop") {
    const before = await page
      .locator(".acct-review-card")
      .getAttribute("data-transaction-id");
    const handle = page.locator(".acct-review-card");
    const box = await handle.boundingBox();
    check(box, "desktop: swipe handle is missing");
    const y = box.y + box.height / 2;
    const startX = box.x + box.width / 2;
    await handle.dispatchEvent("pointerdown", {
      pointerId: 11,
      pointerType: "mouse",
      clientX: startX,
      clientY: y,
      bubbles: true,
    });
    await handle.dispatchEvent("pointermove", {
      pointerId: 11,
      pointerType: "mouse",
      clientX: startX + 160,
      clientY: y,
      bubbles: true,
    });
    await handle.dispatchEvent("pointerup", {
      pointerId: 11,
      pointerType: "mouse",
      clientX: startX + 160,
      clientY: y,
      bubbles: true,
    });
    await page.waitForFunction(
      (transactionId) =>
        document
          .querySelector(".acct-review-card")
          ?.getAttribute("data-transaction-id") !== transactionId,
      before,
      { timeout: 5_000 },
    );
    const after = await page
      .locator(".acct-review-card")
      .getAttribute("data-transaction-id");
    check(
      after !== before,
      "desktop: right swipe did not approve and advance the card",
    );
    await page.getByRole("button", { name: "Edit" }).click();
    await page
      .getByRole("tab", { name: /Expense|Income|Transfer|Invest/ })
      .first()
      .waitFor({
        state: "visible",
      });
    for (const label of [
      "Amount",
      "Account",
      "Date",
      "Payee",
      "Original amount",
      "Original currency",
      "Category",
      "Memo",
    ]) {
      await page.getByLabel(label).last().waitFor({ state: "visible" });
    }
  }

  if (name === "mobile") {
    await page.getByRole("button", { name: "Edit" }).click();
    await page
      .locator(".acct-review-fields-scroll")
      .waitFor({ state: "visible" });
    await page.screenshot({
      path: path.join(out, "mobile-review-edit.png"),
      fullPage: true,
    });
    const editActionsBottom = await page
      .locator(".acct-review-actions")
      .evaluate((actions) => actions.getBoundingClientRect().bottom);
    check(
      editActionsBottom <= viewport.height,
      "mobile edit: action row is cut off below the viewport",
    );
    await page.getByRole("button", { name: "Done" }).click();

    const before = await page
      .locator(".acct-review-card")
      .getAttribute("data-transaction-id");
    const handle = page.locator(".acct-review-card");
    const box = await handle.boundingBox();
    check(box, "mobile: swipe handle is missing");
    const y = box.y + box.height / 2;
    const startX = box.x + box.width * 0.8;
    const endX = box.x + box.width * 0.2;
    await handle.dispatchEvent("pointerdown", {
      pointerId: 17,
      pointerType: "touch",
      clientX: startX,
      clientY: y,
      bubbles: true,
    });
    await handle.dispatchEvent("pointermove", {
      pointerId: 17,
      pointerType: "touch",
      clientX: endX,
      clientY: y,
      bubbles: true,
    });
    await handle.dispatchEvent("pointerup", {
      pointerId: 17,
      pointerType: "touch",
      clientX: endX,
      clientY: y,
      bubbles: true,
    });
    await page.waitForFunction(
      (transactionId) =>
        document
          .querySelector(".acct-review-card")
          ?.getAttribute("data-transaction-id") !== transactionId,
      before,
      { timeout: 5_000 },
    );
    const after = await page
      .locator(".acct-review-card")
      .getAttribute("data-transaction-id");
    check(
      after !== before,
      "mobile: left swipe did not dismiss and advance the card",
    );
  }

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - innerWidth,
  );
  check(overflow <= 1, `${name}: ${overflow}px horizontal overflow`);
  check(
    consoleErrors.length === 0,
    `${name}: console errors: ${consoleErrors.join(" | ")}`,
  );
  await context.close();
}

const browser = await chromium.launch({ headless: true });
try {
  await capture(browser, "desktop", { width: 1440, height: 900 });
  await capture(browser, "mobile", { width: 390, height: 844 });
  await capture(browser, "mobile-compact", { width: 375, height: 667 });
  console.log(`Captured transaction-review evidence in ${out}`);
} finally {
  await browser.close();
}
