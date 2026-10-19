import { test, expect, type Page } from "@playwright/test";

// Contract fixtures are confined to browser interception. No production records,
// uploads, OCR/VLM requests or backend processes are changed by these tests.
const image = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="#eef4e9"/><text x="80" y="120" font-size="32">营养成分表</text><text x="80" y="200" font-size="24">蛋白质 3.2g 5%</text></svg>')}`;
const files = ["正面.png", "背面.png", "降级图.png"].map((name, i) => ({id: `image-${i}`, display_name: name, width_px: 600, height_px: 800, sha256: "a".repeat(64), url: image + `#${i}`}));
const evidence = {id: "ev-1", image_id: "image-0", source_type: "baidu_accurate", text: "蛋白质 3.2g", box_px: {left: 80, top: 170, width: 200, height: 40}, ocr_confidence: 0.9};
const item = {path: "facts.nutritionTables[0].rows[0].amount", raw: "3.2g", value: "3.2", unit: "g", review_reasons: ["OCR_LOW_CONFIDENCE"], evidence: [evidence]};
const task = {id: "frontend-contract", status: "WAITING_REVIEW", standard_mode: "gb28050_2025_precheck", channel: null, source: "upload", files, document_version: 4, check_doc_version: null, stale: false, review_pending: 2, summary: null, error: null, warnings: [], created_at: "2026-10-19T06:00:00Z", updated_at: "2026-10-19T06:00:00Z"};
const review = {
  pipeline: "vlm_zones_ocr_v1", document_version: 4, items: [item], document_id: "doc-1", product_id: null, images: files.map(f => ({id: f.id, fileName: f.display_name, widthPx: f.width_px, heightPx: f.height_px, sha256: f.sha256})), threshold: 0.96,
  table_items: [{table_id: "table-A", code: "TABLE_STRUCTURE_UNCERTAIN", message: "CELL_SPAN_UNSUPPORTED; NRV_ROW_AMBIGUOUS", image_id: "image-0", box_px: evidence.box_px, evidence_refs: ["ev-1"]}],
  extraction: [
    {image_id: "image-0", mode: "regions", regions: [{kind: "nutrition_table", box_px: {left: 70, top: 80, width: 400, height: 350}}, {kind: "ingredients", box_px: {left: 590, top: 20, width: 100, height: 40}}]},
    {image_id: "image-1", mode: "regions", regions: [{kind: "ingredients", box_px: {left: 40, top: 60, width: 220, height: 180}}]},
    {image_id: "image-2", mode: "whole_image_fallback", regions: []},
  ],
};
const field = (id: string, raw: string, value: string, unit: string | null = null, status = "ready") => ({field_id: `nutrition.table-A.${id}`, label: id, group: "nutrition", raw, value, unit, status, review_reasons: [], evidence: [evidence], version: 4});
const fields = [field("basis", "每100克", "per_100g"), field("protein.nutrient", "蛋白质", "protein"), field("protein.amount", "3.2g", "3.2", "g", "needs_review"), field("protein.nrvPercent", "5%", "5", "%"), field("fat.nutrient", "脂肪", "fat"), field("fat.amount", "2.5g", "2.5", "g")];

async function fixture(page: Page, options: {legacy?: boolean; fieldsError?: boolean; mismatchedVersion?: boolean; status?: string} = {}) {
  const mutations: string[] = [];
  await page.route("**/openapi.json", route => route.fulfill({json: {components: {schemas: {CorrectionsRequest: {properties: {no_table_images: {}}}}}}}));
  await page.route("**/api/**", route => {
    const request = route.request();
    if (request.method() !== "GET") { mutations.push(request.method() + " " + request.url()); return route.fulfill({status: 500, json: {error: {message: "Unexpected mutation in UI test"}}}); }
    const path = new URL(request.url()).pathname;
    if (path === "/api/standards") return route.fulfill({json: []});
    if (path.endsWith("/review-items")) return route.fulfill({json: options.legacy ? {...review, pipeline: "legacy_ocr_vision", extraction: [], ocr_fields: fields, items: [{...item, agreement: "agree", vision: {raw: "old vision candidate", value: "999", unit: "g"}}]} : review});
    if (path.endsWith("/fields")) {
      expect(new URL(request.url()).searchParams.get("version")).toBe("4");
      return options.fieldsError ? route.fulfill({status: 503, json: {error: {message: "整表服务暂时不可用"}}}) : route.fulfill({json: {document_version: options.mismatchedVersion ? 3 : 4, fields}});
    }
    if (path.endsWith("/issues")) return route.fulfill({json: []});
    if (path === `/api/audits/${task.id}`) return route.fulfill({json: {...task, status: options.status ?? task.status}});
    return route.fulfill({status: 404, json: {error: {message: "Unexpected API read in UI test"}}});
  });
  return mutations;
}

test("zones are optional, image-bound, transparent and separate from OCR markers", async ({page}) => {
  const mutations = await fixture(page);
  await page.goto(`/audits/${task.id}/review`);
  const details = page.locator("details.extraction-details");
  await expect(details).not.toHaveAttribute("open");
  await expect(page.locator(".box-zone")).toHaveCount(0);
  await expect(page.getByRole("button", {name: "显示分区", exact: true})).toHaveAttribute("aria-pressed", "false");
  await details.locator("summary").click();
  await expect(details.getByText("分区未完成，已降级为整图 OCR；没有可展示的分区框。")).toBeVisible();
  await expect(details.getByRole("button")).toHaveCount(2); // out-of-image region is rejected
  await details.getByRole("button", {name: "定位背面.png的配料表区域1"}).click();
  await expect(page.locator(".viewer-stage img")).toHaveAttribute("alt", "背面.png");
  await expect(page.locator(".box-zone")).toHaveCount(1);
  expect(await page.locator(".box-zone").evaluate(node => getComputedStyle(node).fill)).toBe("none");
  await expect(page.getByText("VLM 分区", {exact: true})).toBeVisible();
  await page.getByRole("button", {name: "隐藏分区", exact: true}).click();
  await expect(page.locator(".box-zone")).toHaveCount(0);
  await page.getByRole("button", {name: "隐藏标记", exact: true}).click();
  await expect(page.locator(".viewer rect")).toHaveCount(0);
  expect(mutations).toEqual([]);
});

test("one editable table includes ready rows and keeps OCR evidence collapsed", async ({page}) => {
  await fixture(page, {legacy: true});
  await page.goto(`/audits/${task.id}/review`);
  const editor = page.locator(".nutrition-editor");
  await expect(editor).toHaveCount(1);
  await expect(editor.locator(".nutrition-data-row")).toHaveCount(2);
  await expect(editor.getByRole("cell", {name: "蛋白质", exact: true})).toBeVisible();
  await expect(editor.getByRole("cell", {name: "脂肪", exact: true})).toBeVisible();
  await expect(page.locator(".nutrition-preview, .table-review, .field-card")).toHaveCount(0);
  await expect(editor.getByRole("checkbox")).toHaveCount(1);
  await expect(editor.locator("details")).not.toHaveAttribute("open");
  await expect(page.getByText(/两路一致|AI 视觉候选|old vision candidate|视觉兜底前/)).toHaveCount(0);
  await editor.getByRole("button", {name: "查看营养表1第1行识别依据"}).click();
  await expect(editor.getByText("OCR 原文：3.2g", {exact: true})).toBeVisible();
  await editor.getByRole("button", {name: "查看营养表1第1行识别依据"}).click();
  await page.screenshot({path: "test-results/unified-review-desktop.png", fullPage: true});
  await page.setViewportSize({width: 375, height: 812});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.screenshot({path: "test-results/unified-review-mobile.png", fullPage: true});
});

for (const failure of ["error", "version mismatch"]) test(`table confirmation is unavailable on preview ${failure}`, async ({page}) => {
  const mutations = await fixture(page, {fieldsError: failure === "error", mismatchedVersion: failure !== "error"});
  await page.goto(`/audits/${task.id}/review`);
  if (failure === "error") await expect(page.getByText("无法读取整表预览", {exact: true})).toBeVisible({timeout: 12000});
  await expect(page.getByText("整表预览尚未加载，暂不能确认全部行列。")).toBeVisible();
  await page.getByRole("combobox", {name: /^处理方式/}).click();
  await expect(page.locator(".ant-select-item-option-disabled").filter({hasText: "表格已完整，确认全部行列"})).toBeVisible();
  await expect(page.getByRole("button", {name: "提交修正并重新检查"})).toBeDisabled();
  expect(mutations).toEqual([]);
});

for (const [status, title] of [["QUEUED", "等待开始识别"], ["EXTRACTING", "正在进行包装分区与文字识别"], ["CHECKING", "正在用已确认数据进行规则检查"]]) test(`result stage reflects ${status}`, async ({page}) => {
  await fixture(page, {status});
  await page.goto(`/audits/${task.id}/results`);
  await expect(page.getByText(title, {exact: true})).toBeVisible();
  await expect(page.getByRole("progressbar")).toHaveCount(0);
  await expect(page.getByRole("button", {name: "导出 JSON"})).toBeDisabled();
});

async function captureCorrection(page: Page) {
  const requests: Record<string, any>[] = [];
  await page.route(`**/api/audits/${task.id}/corrections`, route => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({status: 422, json: {error: {code: "CAPTURE_ONLY", message: "测试请求已拦截，未写入真实服务"}}});
  });
  return requests;
}

test("enterprise basis copy is scoped to its rule and preserves result and other authorities", async ({page}) => {
  const mutations = await fixture(page);
  const basisIssue = {issue_id: "basis", rule_id: "COURSE-100G-001", layer: "L1", status: "FAIL", title: "旧标题", message: "本课程交付的营养表须明确写每100g", review_decision: "open", evidence: [], calculation: {provided_basis: "per_serving", provided_header: "每份"}, regulation: {verification_status: "course_requirement", note: "非国标条款"}};
  await page.route(`**/api/audits/${task.id}`, route => route.fulfill({json: {...task, status: "COMPLETED", check_doc_version: 4, review_pending: 0}}));
  await page.route("**/issues", route => route.fulfill({json: [basisIssue, {...basisIssue, issue_id: "other", rule_id: "OTHER-INTERNAL", message: "其他内部规则原文", title: "其他内部规则", calculation: null}]}));
  await page.goto(`/audits/${task.id}/results`);
  const detail = page.locator(".detail-panel");
  await expect(detail.getByRole("heading", {name: "企业渠道要求：营养表采用每100g口径", exact: true})).toBeVisible();
  await expect(detail.getByText("企业渠道要求（非国标条款）", {exact: true})).toBeVisible();
  await expect(detail.getByText(/当前识别为“每份”/)).toBeVisible();
  await expect(detail.getByText("问题", {exact: true})).toBeVisible();
  await expect(page.getByText(/本课程交付/)).toHaveCount(0);
  await page.locator(".issue-row").filter({hasText: "其他内部规则原文"}).click();
  await expect(detail.getByText("课程 / 内部要求，非国标条款", {exact: true})).toBeVisible();
  await expect(detail.getByText("其他内部规则原文", {exact: true})).toBeVisible();
  expect(mutations).toEqual([]);
});

test("result evidence separates manual records, folds OCR and preserves original-index location", async ({page}) => {
  const mutations = await fixture(page);
  const records = [
    {id: "manual-name", source_type: "manual", text: "人工确认：能量", image_id: null, box_px: null},
    {...evidence, id: "ocr-fat", text: "脂肪 5.8g"},
    {id: "manual-value", source_type: "manual", text: "人工确认：822 kJ", image_id: null, box_px: null},
    {...evidence, id: "ocr-energy", image_id: "image-1", text: "能量 822kJ", box_px: {left: 100, top: 300, width: 240, height: 50}},
  ];
  const issue = {issue_id: "energy", rule_id: "NRV-100G-ENERGY", layer: "L1", status: "REVIEW", title: "能量", message: "能量待核对", review_decision: "open", evidence: records, calculation: null, doc_version: 4, regulation: {verification_status: "pending", note: "待核验"}};
  await page.route(`**/api/audits/${task.id}`, route => route.fulfill({json: {...task, status: "PARTIAL", check_doc_version: 4, review_pending: 0}}));
  await page.route("**/issues", route => route.fulfill({json: [issue, {...issue, issue_id: "protein", rule_id: "NRV-100G-PROTEIN", message: "蛋白质待核对"}]}));
  await page.goto(`/audits/${task.id}/results`);
  const section = page.getByRole("region", {name: "核对记录与原图证据", exact: true});
  const ocr = section.locator("details.ocr-evidence");
  await expect(section.getByText("人工确认：822 kJ", {exact: true})).toBeVisible();
  await expect(section.getByRole("region", {name: "人工确认记录", exact: true}).getByRole("button")).toHaveCount(0);
  await expect(ocr).not.toHaveAttribute("open");
  await expect(section.getByText("脂肪 5.8g", {exact: true})).not.toBeVisible();
  await ocr.locator("summary").focus();
  await page.keyboard.press("Enter");
  await expect(ocr).toHaveAttribute("open", "");
  await expect(ocr.getByRole("button")).toHaveCount(2);
  await ocr.getByRole("button").filter({hasText: "能量 822kJ"}).click();
  await expect(page.locator(".viewer-stage img")).toHaveAttribute("alt", "背面.png");
  await expect(page.locator('.viewer rect.box-active[x="100"][y="300"]')).toHaveCount(1);
  await page.locator(".issue-row").filter({hasText: "蛋白质待核对"}).click();
  await expect(ocr).not.toHaveAttribute("open");
  await page.screenshot({path: "test-results/result-evidence-desktop.png", fullPage: true});
  await page.setViewportSize({width: 375, height: 812});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.screenshot({path: "test-results/result-evidence-mobile.png", fullPage: true});
  expect(mutations).toEqual([]);
});

test("one acknowledgement submits pending cells and structure together, without replacement", async ({page}) => {
  await fixture(page);
  const requests = await captureCorrection(page);
  await page.goto(`/audits/${task.id}/review`);
  const editor = page.locator(".nutrition-editor");
  await editor.getByRole("textbox", {name: "营养表1第1行含量", exact: true}).fill("4.2");
  await editor.getByRole("checkbox").check();
  await page.getByRole("textbox", {name: "复核人", exact: true}).fill("前端验证");
  await page.getByRole("button", {name: "提交修正并重新检查"}).click();
  await expect(page.getByText("测试请求已拦截，未写入真实服务", {exact: true})).toBeVisible();
  expect(requests).toHaveLength(1);
  expect(requests[0].decisions).toMatchObject([{path: item.path, action: "correct", originalValue: "3.2", correctedValue: "4.2", sourceEvidenceRefs: ["ev-1"]}]);
  expect(requests[0].tableDecisions).toMatchObject([{tableId: "table-A", action: "confirm_complete"}]);
  expect(requests[0].table_checked_all).toBe(true);
  expect(requests[0].manual_tables).toEqual([]);
});

test("numeric-only NRV still rejects percent signs and edits reset the single acknowledgement", async ({page}) => {
  const mutations = await fixture(page);
  await page.goto(`/audits/${task.id}/review`);
  const editor = page.locator(".nutrition-editor");
  await editor.getByRole("button", {name: "调整行结构", exact: true}).click();
  const input = editor.getByRole("textbox", {name: "营养表1第1行NRV%", exact: true});
  const submit = page.getByRole("button", {name: "提交修正并重新检查"});
  for (const invalid of ["10%", "10％", "-1", "1e2", "10%%", "10 "]) {
    await input.fill(invalid);
    await expect(input).toHaveValue(invalid);
    await expect(input).toHaveAttribute("aria-invalid", "true");
    await expect(editor.getByRole("alert")).toContainText("NRV% 只填非负数字");
    await expect(submit).toBeDisabled();
  }
  await input.fill("10");
  await expect(input).toHaveAttribute("aria-invalid", "false");
  await editor.getByRole("checkbox").check();
  await expect(page.locator(".submission-hint")).toHaveText("暂不能提交：请填写复核人");
  await page.getByRole("textbox", {name: "复核人", exact: true}).fill("前端验证");
  await expect(submit).toBeEnabled();
  await input.fill("10%");
  await expect(editor.getByRole("checkbox")).not.toBeChecked();
  await expect(submit).toBeDisabled();
  expect(mutations).toEqual([]);
});

test("structure adjustment reuses edits and exclusively sends the full replacement", async ({page}) => {
  await fixture(page);
  const requests = await captureCorrection(page);
  await page.goto(`/audits/${task.id}/review`);
  const editor = page.locator(".nutrition-editor");
  await editor.getByRole("textbox", {name: "营养表1第1行含量", exact: true}).fill("4.2");
  await editor.getByRole("button", {name: "调整行结构", exact: true}).click();
  await expect(editor.getByRole("textbox", {name: "营养表1第1行含量", exact: true})).toHaveValue("4.2");
  await editor.getByRole("combobox", {name: "营养表1表头基准", exact: true}).selectOption("per_serving");
  const serving = editor.getByRole("textbox", {name: "营养表1每份质量", exact: true});
  await serving.fill("0");
  await expect(serving).toHaveAttribute("aria-invalid", "true");
  await serving.fill("43");
  await editor.getByRole("button", {name: "添加营养项", exact: true}).click();
  await editor.getByRole("combobox", {name: "营养表1第3行营养项", exact: true}).selectOption("sodium");
  await editor.getByRole("textbox", {name: "营养表1第3行含量", exact: true}).fill("42");
  await editor.getByRole("textbox", {name: "营养表1第3行单位", exact: true}).fill("mg");
  await editor.getByRole("textbox", {name: "营养表1第3行NRV%", exact: true}).fill("2");
  await editor.getByRole("checkbox").check();
  await page.getByRole("textbox", {name: "复核人", exact: true}).fill("前端验证");
  await page.getByRole("button", {name: "提交修正并重新检查"}).click();
  await expect(page.getByText("测试请求已拦截，未写入真实服务", {exact: true})).toBeVisible();
  expect(requests[0].decisions).toEqual([]);
  expect(requests[0].tableDecisions).toEqual([]);
  expect(requests[0].manual_tables).toMatchObject([{table_id: "table-A", basis: "per_serving", serving_g: "43", rows: [{nutrient: "protein", amount: "4.2", nrv_percent: "5"}, {nutrient: "fat", amount: "2.5", nrv_percent: null}, {nutrient: "sodium", amount: "42", unit: "mg", nrv_percent: "2"}]}]);
});

test("multiple tables remain bound to their own image, values and request paths", async ({page}) => {
  await fixture(page);
  const evidence2 = {...evidence, id: "ev-2", image_id: "image-1", text: "钠42mg"};
  const item2 = {...item, path: "facts.nutritionTables[1].rows[0].amount", raw: "42mg", value: "42", unit: "mg", evidence: [evidence2]};
  const fields2 = [field("basis", "每100克", "per_100g"), field("sodium.nutrient", "钠", "sodium"), field("sodium.amount", "42mg", "42", "mg", "needs_review")].map(f => ({...f, field_id: f.field_id.replace("table-A", "table-B"), evidence: [evidence2]}));
  await page.route(`**/api/audits/${task.id}/review-items`, route => route.fulfill({json: {...review, items: [item, item2], table_items: [...review.table_items, {...review.table_items[0], table_id: "table-B", image_id: "image-1", evidence_refs: ["ev-2"]}]}}));
  await page.route(`**/api/audits/${task.id}/fields?*`, route => route.fulfill({json: {document_version: 4, fields: [...fields, ...fields2]}}));
  const requests = await captureCorrection(page);
  await page.goto(`/audits/${task.id}/review`);
  await expect(page.locator(".nutrition-editor")).toHaveCount(2);
  const second = page.locator(".nutrition-editor").nth(1);
  await second.getByRole("textbox", {name: "营养表2第1行含量", exact: true}).fill("43");
  await expect(page.locator(".viewer-stage img")).toHaveAttribute("alt", "背面.png");
  await second.getByRole("checkbox").check();
  await page.getByRole("textbox", {name: "复核人", exact: true}).fill("前端验证");
  await page.getByRole("button", {name: "提交修正并重新检查"}).click();
  await expect(page.getByText("测试请求已拦截，未写入真实服务", {exact: true})).toBeVisible();
  expect(requests).toHaveLength(1);
  expect(requests[0].image_id).toBe("image-1");
  expect(requests[0].decisions).toMatchObject([{path: item2.path, correctedValue: "43", sourceEvidenceRefs: ["ev-2"]}]);
  expect(requests[0].tableDecisions).toMatchObject([{tableId: "table-B"}]);
});

test("a mismatched path/value association is not silently merged into a table", async ({page}) => {
  await fixture(page);
  await page.route(`**/api/audits/${task.id}/review-items`, route => route.fulfill({json: {...review, items: [{...item, value: "999"}]}}));
  await page.goto(`/audits/${task.id}/review`);
  await expect(page.locator(".field-card textarea")).toHaveValue("999");
  await expect(page.locator(".nutrition-editor")).toHaveCount(0);
  await expect(page.getByText("整表预览尚未加载，暂不能确认全部行列。")).toBeVisible();
});

test("a rejected structure confirmation preserves edits and explains the existing replacement path", async ({page}) => {
  await fixture(page);
  await page.route(`**/api/audits/${task.id}/corrections`, route => route.fulfill({status: 422, json: {error: {code: "INVALID_CORRECTION", message: "Stale table decision: table-A"}}}));
  await page.goto(`/audits/${task.id}/review`);
  const editor = page.locator(".nutrition-editor");
  const amount = editor.getByRole("textbox", {name: "营养表1第1行含量", exact: true});
  await amount.fill("4.2");
  await editor.getByRole("checkbox").check();
  await page.getByRole("textbox", {name: "复核人", exact: true}).fill("前端验证");
  await page.getByRole("button", {name: "提交修正并重新检查"}).click();
  await expect(page.getByText("当前后端未接受本表的结构确认，草稿已保留。请在同一张表点击“调整行结构”，对照原图核对后使用整表替换提交。", {exact: true})).toBeVisible();
  await expect(amount).toHaveValue("4.2");
  await editor.getByRole("button", {name: "调整行结构", exact: true}).click();
  await expect(amount).toHaveValue("4.2");
});

test("duplicate nutrient names retain distinct rows and submit the corrected row path", async ({page}) => {
  await fixture(page);
  const secondEvidence = {...evidence, id: "ev-duplicate", text: "蛋白质2.5g"};
  const secondName = {...item, path: "facts.nutritionTables[0].rows[1].nutrient", raw: "蛋白质", value: "protein", unit: null, evidence: [secondEvidence]};
  const secondAmount = {...item, path: "facts.nutritionTables[0].rows[1].amount", raw: "2.5g", value: "2.5", evidence: [secondEvidence]};
  const duplicateFields = [fields[0], fields[1], fields[2], {...fields[1], status: "needs_review", evidence: [secondEvidence]}, {...fields[2], raw: "2.5g", value: "2.5", evidence: [secondEvidence]}];
  await page.route(`**/api/audits/${task.id}/review-items`, route => route.fulfill({json: {...review, items: [item, secondName, secondAmount]}}));
  await page.route(`**/api/audits/${task.id}/fields?*`, route => route.fulfill({json: {document_version: 4, fields: duplicateFields}}));
  const requests = await captureCorrection(page);
  await page.goto(`/audits/${task.id}/review`);
  const editor = page.locator(".nutrition-editor");
  await expect(editor.locator(".nutrition-data-row")).toHaveCount(2);
  await expect(editor.getByRole("textbox", {name: "营养表1第1行含量", exact: true})).toHaveValue("3.2");
  await expect(editor.getByRole("textbox", {name: "营养表1第2行含量", exact: true})).toHaveValue("2.5");
  await editor.getByRole("combobox", {name: "营养表1第2行营养项", exact: true}).selectOption("fat");
  await editor.getByRole("checkbox").check();
  await page.getByRole("textbox", {name: "复核人", exact: true}).fill("前端验证");
  await page.getByRole("button", {name: "提交修正并重新检查"}).click();
  await expect(page.getByText("测试请求已拦截，未写入真实服务", {exact: true})).toBeVisible();
  expect(requests[0].decisions).toHaveLength(3);
  expect(requests[0].decisions[1]).toMatchObject({path: secondName.path, correctedValue: "fat", sourceEvidenceRefs: ["ev-duplicate"]});
});

async function netFixture(page: Page, raw: string, value: string, unit: string | null) {
  const mutations = await fixture(page);
  const net = {...item, path: "facts.netContent", raw, value, unit, evidence: [{...evidence, text: raw}]};
  await page.route(`**/api/audits/${task.id}/review-items`, route => route.fulfill({json: {...review, items: [net], table_items: []}}));
  return {net, mutations};
}

test("standard-number net content is not prefilled; manual correction keeps original evidence", async ({page}) => {
  const raw = "号：GB/T22699○配料表：玉米糁（≥45%）";
  const {net} = await netFixture(page, raw, "22699", null);
  const requests = await captureCorrection(page);
  await page.goto(`/audits/${task.id}/review`);
  const card = page.locator('[id="facts.netContent"]');
  const value = card.getByRole("textbox", {name: "净含量数值", exact: true});
  const unit = card.getByRole("combobox", {name: "净含量单位", exact: true});
  await expect(value).toHaveValue("");
  await expect(unit).toHaveValue("");
  await expect(card.getByText("净含量疑似字段错配", {exact: true})).toBeVisible();
  await expect(card.getByText(raw, {exact: true})).toBeVisible();
  await expect(card.getByRole("button", {name: "恢复当前值", exact: true})).toBeDisabled();
  await expect(card.getByRole("checkbox")).toBeDisabled();
  await unit.selectOption("g");
  await expect(value).toHaveValue(""); // selecting a unit never invents a value
  await expect(card.getByRole("checkbox")).toBeDisabled();
  await value.fill("43");
  await expect(card.getByRole("checkbox")).toBeEnabled();
  await card.getByRole("checkbox").check();
  await page.getByRole("textbox", {name: "复核人", exact: true}).fill("前端验证");
  await page.getByRole("button", {name: "提交修正并重新检查"}).click();
  await expect(page.getByText("测试请求已拦截，未写入真实服务", {exact: true})).toBeVisible();
  expect(requests).toHaveLength(1);
  expect(requests[0].decisions).toMatchObject([{path: "facts.netContent", action: "correct", originalRaw: net.raw, originalValue: "22699", originalUnit: null, sourceEvidenceRefs: ["ev-1"], manualText: "43", correctedValue: "43", correctedUnit: "g"}]);
  expect(requests[0].manual_tables).toEqual([]);
});

test("net content requires a numeric value and explicit unit without silent conversions", async ({page}) => {
  const {mutations} = await netFixture(page, "净含量：43g", "43", null);
  await page.goto(`/audits/${task.id}/review`);
  const card = page.locator('[id="facts.netContent"]');
  const value = card.getByRole("textbox", {name: "净含量数值", exact: true});
  const unit = card.getByRole("combobox", {name: "净含量单位", exact: true});
  await expect(value).toHaveValue("43");
  await expect(unit).toHaveAttribute("aria-invalid", "true");
  await expect(card.getByRole("checkbox")).toBeDisabled();
  await expect(card.getByText("净含量单位必填，请按原图选择；不能留空。", {exact: true})).toBeVisible();
  await unit.selectOption("g");
  for (const invalid of ["43g", " 43", "-1", "4e1", "0043"]) {
    await value.fill(invalid);
    await expect(value).toHaveValue(invalid);
    await expect(value).toHaveAttribute("aria-invalid", "true");
    await expect(card.getByRole("checkbox")).toBeDisabled();
  }
  await value.fill("43");
  await card.getByRole("checkbox").check();
  await unit.selectOption("");
  await expect(card.getByRole("checkbox")).not.toBeChecked();
  await expect(card.getByRole("checkbox")).toBeDisabled();
  await card.screenshot({path: "test-results/net-content-required-desktop.png"});
  await page.setViewportSize({width: 375, height: 812});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await card.screenshot({path: "test-results/net-content-required-mobile.png"});
  expect(mutations).toEqual([]);
});

for (const [raw, value] of [
  ["净含量：43g○产品标准GB/T22699", "43"],
  ["净含量：22699g○产品标准GB/T22699", "22699"],
  ["1000000g", "1000000"],
]) test(`valid net-content evidence is retained: ${raw}`, async ({page}) => {
  await netFixture(page, raw, value, "g");
  await page.goto(`/audits/${task.id}/review`);
  const card = page.locator('[id="facts.netContent"]');
  await expect(card.getByRole("textbox", {name: "净含量数值", exact: true})).toHaveValue(value);
  await expect(card.getByText("净含量疑似字段错配", {exact: true})).toHaveCount(0);
  await expect(card.getByRole("checkbox")).toBeEnabled();
});
