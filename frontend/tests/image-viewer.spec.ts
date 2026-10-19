import { test, expect } from "@playwright/test";

test("nutrition evidence leaves the text clear and markers can be hidden", async ({page, request}) => {
  const tasks = await (await request.get("/api/audits?limit=50")).json();
  const task = tasks.items.find((t: {status: string}) => t.status === "WAITING_REVIEW");
  test.skip(!task, "Requires an existing review task; never uploads or runs OCR");
  await page.goto(`/audits/${task.id}/review`);
  const queue = await (await request.get(`/api/audits/${task.id}/review-items`)).json();
  const net = queue.items.find((item: {path: string}) => item.path === "facts.netContent");
  if (net?.value === "22699" && net.raw?.includes("GB/T22699")) {
    const card = page.locator('[id="facts.netContent"]');
    await expect(card.getByRole("textbox", {name: "净含量数值", exact: true})).toHaveValue("");
    await expect(card.getByRole("combobox", {name: "净含量单位", exact: true})).toHaveValue("");
    await expect(card.getByRole("checkbox")).toBeDisabled();
    await expect(card.getByText(net.raw, {exact: true})).toBeVisible();
    await card.screenshot({path: "test-results/live-net-content-guard.png"});
  }
  const table = queue.table_items.find((t: {table_id: string | null}) => !!t.table_id);
  if (table) {
    const fields = await (await request.get(`/api/audits/${task.id}/fields?version=${queue.document_version}`)).json();
    const rows = fields.fields.filter((f: {field_id: string}) => f.field_id.startsWith(`nutrition.${table.table_id}.`) && f.field_id.endsWith(".nutrient"));
    const preview = page.locator(".nutrition-editor").first();
    await expect(preview.locator(".nutrition-data-row")).toHaveCount(rows.length);
    await expect(preview.getByRole("checkbox")).toHaveCount(1);
    await expect(page.locator(".nutrition-preview")).toHaveCount(0);
    await expect(page.getByText(/两路一致|AI 视觉候选|视觉兜底前/)).toHaveCount(0);
    await preview.screenshot({path: "test-results/live-nutrition-preview.png"});
  }
  const header = page.locator(".nutrition-editor").first().getByRole("textbox", {name: /第1行含量/});
  await expect(header).toBeVisible();
  await header.focus();
  const active = page.locator(".viewer rect.box-active");
  await expect(active.first()).toBeAttached();
  const fills = await page.locator(".viewer rect.box").evaluateAll(nodes => nodes.map(n => getComputedStyle(n).fill));
  expect(fills.length).toBeGreaterThan(0);
  expect(fills.every(fill => fill === "none")).toBe(true);
  const geometries = await page.locator(".viewer rect.box").evaluateAll(nodes => nodes.map(n => [n.classList.contains("box-vision"), n.classList.contains("box-region"), ...["x", "y", "width", "height"].map(key => n.getAttribute(key))].join(":")));
  expect(new Set(geometries).size).toBe(geometries.length);
  const source = await page.locator(".viewer-stage img").getAttribute("src");
  await page.locator(".viewer").scrollIntoViewIfNeeded();
  await page.locator(".viewer").screenshot({path: "test-results/nutrition-outlines.png"});
  await page.getByRole("button", {name: "隐藏标记", exact: true}).click();
  await expect(page.locator(".viewer rect.box")).toHaveCount(0);
  expect(await page.locator(".viewer-stage img").getAttribute("src")).toBe(source);
  await page.locator(".viewer").screenshot({path: "test-results/nutrition-original.png"});
  await page.getByRole("button", {name: "显示标记", exact: true}).click();
  await expect(active.first()).toBeAttached();
});
