import { test, expect } from "@playwright/test";

test("opens the actual workbench without login or samples", async ({page}) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/audits$/);
  await expect(page.getByRole("heading", {name: "包装审核工作台"})).toBeVisible();
  await expect(page.getByLabel("访问口令")).toHaveCount(0);
  await expect(page.getByText("Mock 数据", {exact: true})).toHaveCount(0);
  await expect(page.getByRole("combobox")).toHaveCount(0);
  await expect(page.getByRole("button", {name: "用样本创建任务"})).toHaveCount(0);
  expect((await page.request.get("/api/audits")).status()).toBe(200);
  expect(await page.context().cookies()).toEqual([]);
  await page.goto("/login");
  await expect(page).toHaveURL(/\/audits$/);
});

test("upload validation rejects bad types and excess files before sending", async ({page}) => {
  await page.goto("/audits");
  await page.locator('input[type="file"]').setInputFiles({name: "bad.txt", mimeType: "text/plain", buffer: Buffer.from("bad")});
  await expect(page.getByText("「bad.txt」不是 PNG 或 JPG 图片")).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles(Array.from({length: 4}, (_, i) => ({name: `image${i}.png`, mimeType: "image/png", buffer: Buffer.from("image")})));
  await expect(page.getByText("每个任务最多 3 张图片，请减少后重新选择")).toBeVisible();
  await expect(page.getByRole("button", {name: "开始审核"})).toBeDisabled();
});

test("invalid image reaches real API, rejects locally and never creates a task", async ({page}) => {
  await page.goto("/audits");
  const before = await (await page.request.get("/api/audits")).json();
  await page.locator('input[type="file"]').setInputFiles({name: "invalid.png", mimeType: "image/png", buffer: Buffer.from("not an image")});
  const responsePromise = page.waitForResponse(r => r.url().endsWith("/api/audits") && r.request().method() === "POST");
  await page.getByRole("button", {name: "开始审核"}).click();
  const response = await responsePromise;
  expect(response.status()).toBe(415);
  const body = await response.json();
  await expect(page.getByText(body.error.message, {exact: true})).toBeVisible();
  expect((await (await page.request.get("/api/audits")).json()).total).toBe(before.total);
  await expect(page).toHaveURL(/\/audits$/);
});

test("service errors stay on the workbench and do not fall back to sample data", async ({page}) => {
  await page.route("**/api/audits?*", r => r.fulfill({status: 503, json: {error: {code: "TEST_UNAVAILABLE", message: "服务暂时不可用，请稍后重试"}}}));
  await page.goto("/audits");
  await expect(page.getByText("服务暂时不可用，请稍后重试")).toBeVisible({timeout: 12000});
  await expect(page.locator(".task-row")).toHaveCount(0);
  await expect(page).toHaveURL(/\/audits$/);
});

test("desktop and mobile workbench remain readable", async ({page}) => {
  await page.goto("/audits");
  await expect(page.getByRole("heading", {name: "审核任务"})).toBeVisible();
  await page.screenshot({path: "test-results/real-workbench-desktop.png", fullPage: true});
  await page.setViewportSize({width: 375, height: 812});
  await page.emulateMedia({reducedMotion: "reduce"});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.screenshot({path: "test-results/real-workbench-mobile.png", fullPage: true});
});
