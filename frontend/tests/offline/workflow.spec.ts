import { test, expect } from "@playwright/test";

test("synthetic upload → human correction → real rules → decisions → report", async ({page, request}) => {
  // A blank 120×100 test image, not a food label. Only extraction is synthetic.
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAHgAAABkCAIAAADCEmNlAAAA80lEQVR4nO3SQQ0AMAwDsW78OXcodi+bQKRTzu4O/91gA6E7Hh0ROiJ0ROiI0BGhI0JHhI4IHRE6InRE6IjQEaEjQkeEjggdEToidEToiNARoSNCR4SOCB0ROiJ0ROiI0BGhI0JHhI4IHRE6InRE6IjQEaEjQkeEjggdEToidEToiNARoSNCR4SOCB0ROiJ0ROiI0BGhI0JHhI4IHRE6InRE6IjQEaEjQkeEjggdEToidEToiNARoSNCR4SOCB0ROiJ0ROiI0BGhI0JHhI4IHRE6InRE6IjQEaEjQkeEjggdEToidEToiNARoSNCR4SOCD2NBx2BA8XhW+JZAAAAAElFTkSuQmCC", "base64");
  await page.goto("/audits");
  await page.locator('input[type="file"]').setInputFiles({name: "synthetic-test.png", mimeType: "image/png", buffer: png});
  const created = page.waitForResponse(r => r.url().endsWith("/api/audits") && r.request().method() === "POST");
  await page.getByRole("button", {name: "开始审核", exact: true}).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const task = await response.json();
  const prefix = `/api/audits/${task.id}`;
  await expect(page.getByRole("button", {name: /去复核/})).toBeVisible();
  await page.getByRole("button", {name: /去复核/}).click();
  await expect(page.locator(".nutrition-editor")).toBeVisible();
  await expect(page.getByRole("button", {name: /确认两路一致项/})).toHaveCount(0);
  await expect(page.getByText("AI 视觉候选，待确认", {exact: true})).toHaveCount(0);
  const field = page.locator(".nutrition-editor").first();
  await field.getByRole("button", {name: /第2行识别依据/}).click();
  await expect(page.getByText("OCR 可信度低于96%", {exact: true})).toBeVisible();
  await field.getByRole("textbox", {name: /第2行含量/}).fill("3.2");
  await field.getByRole("checkbox").check();
  await page.getByRole("textbox", {name: "复核人", exact: true}).fill("离线测试");
  await page.getByRole("button", {name: "提交修正并重新检查"}).click();
  await expect(page).toHaveURL(new RegExp(`/audits/${task.id}/results$`));
  await expect(page.getByRole("button", {name: "导出 JSON"})).toBeEnabled();

  // Reload proves corrections and the queue resume were persisted by the API.
  await page.reload();
  const fields = await (await request.get(prefix + "/fields")).json();
  expect(fields.fields.find((f: {field_id: string}) => f.field_id.endsWith("protein.amount"))).toMatchObject({value: "3.2", status: "human_confirmed"});
  const issues = await (await request.get(prefix + "/issues")).json();
  expect(issues.find((i: {rule_id: string}) => i.rule_id === "NRV-100G-PROTEIN")).toMatchObject({status: "PASS", calculation: {expected_percent: "5"}, regulation: {verification_status: "pending", clause_id: null}});
  await expect(page.locator(".issue-row").first()).toBeVisible();
  await page.locator(".issue-row").first().click();
  await page.getByRole("button", {name: "确认问题", exact: true}).click();
  await expect(page.getByRole("button", {name: "撤销标记", exact: true})).toBeVisible();
  const revoke = page.waitForResponse(r => r.url().endsWith("/decision") && r.request().method() === "PUT");
  await page.getByRole("button", {name: "撤销标记", exact: true}).click();
  expect((await revoke).status()).toBe(200);
  await expect(page.getByRole("button", {name: "撤销标记", exact: true})).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("button", {name: "撤销标记", exact: true})).toHaveCount(0);

  const download = page.waitForEvent("download");
  await page.getByRole("button", {name: "导出 JSON"}).click();
  const stream = await (await download).createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  const report = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  expect(report.task).toMatchObject({document_version: 1, check_doc_version: 1, stale: false});
  expect(report.confirmed_fields.some((id: string) => id.endsWith("protein.amount"))).toBe(true);
  expect(report.issues.every((i: {review_decision: string}) => i.review_decision === "open")).toBe(true);
  expect(report.corrections).toHaveLength(1);
  expect(report.unchecked.length).toBeGreaterThan(0);
  const html = await request.get(prefix + "/report?format=html");
  expect(html.status()).toBe(200);
  expect(await html.text()).toContain("查看标准原文");
});
