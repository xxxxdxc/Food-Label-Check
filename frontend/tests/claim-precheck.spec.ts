import { test, expect, type Page } from "@playwright/test";

// All writes and model-facing actions are intercepted. No production retries.
const image = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="#eef4e9"/><text x="60" y="160" font-size="28">非油炸</text></svg>')}`;
const ev = {id: "claim-source", image_id: "image-0", source_type: "baidu_accurate", text: "非油炸", box_px: {left: 60, top: 120, width: 160, height: 45}, ocr_confidence: 0.99};
const task = {id: "claim-contract", status: "PARTIAL", standard_mode: "gb28050_2025_precheck", channel: null, source: "upload", files: [{id: "image-0", display_name: "包装.png", width_px: 600, height_px: 800, sha256: "a".repeat(64), url: image}], document_version: 2, check_doc_version: 2, stale: false, review_pending: 0, summary: null, error: null, warnings: [], created_at: "2026-10-19T06:00:00Z", updated_at: "2026-10-19T06:00:00Z"};
const item = {claim_id: "claim-A", claim_text: "非油炸", claim_category: "process", source_evidence_refs: [ev.id], status: "completed", verdict: "insufficient_evidence", reason: "没有该产品的工艺证明。", supporting_evidence_refs: [], reference_citations: [], missing_materials: ["该产品生产工艺证明"], model_requested: "gpt-6.1-sol", model_actual: "gpt-6.1-sol", reference_set_version: "ref-v1", prompt_version: "prompt-v1"};
const response = {document_version: 2, schema_version: "claim-precheck-v1", status: "completed", stale: false, items: [item, {...item, claim_id: "claim-B", reason: "第二处声称独立意见。"}], reference_notice: "当前资料仅作内部核查提示，不是产品事实证明。", error_code: null, error_message: null};
const failedItems = response.items.map(entry => ({...entry, status: "unavailable", verdict: null, reason: null}));
const partial = {...response, status: "partial", items: [item, {...failedItems[1], error_code: "UNSUPPORTED_VERDICT", error_message: "模型给出矛盾结论，但缺少可支撑判定的准入资料，未采纳该结论"}], validator_version: "claim-validator-v2", revalidated_from_attempt_id: 3};
const issue = {issue_id: "CLAIM-UNKNOWN:1", rule_id: "CLAIM-UNKNOWN", claim_id: "claim-A", layer: "L3", status: "REVIEW", title: "未知", message: "旧声称说明", review_decision: "open", evidence: [ev], calculation: {}, doc_version: 2, regulation: {verification_status: "not_applicable", standard: "", edition: "", clause_id: null, note: "未覆盖"}, suggestion: null};

async function fixture(page: Page, options: {data?: any; task?: any; issues?: any[]; readError?: boolean; retryError?: boolean; polling?: boolean; retryData?: any} = {}) {
  const mutations: string[] = [];
  const reads: string[] = [];
  await page.route("**/api/**", route => {
    const req = route.request();
    const url = new URL(req.url());
    if (req.method() !== "GET") {
      mutations.push(req.method() + " " + url.pathname + url.search);
      if (url.pathname.endsWith("/claim-prechecks/retry")) return options.retryError
        ? route.fulfill({status: 409, json: {error: {code: "STALE_RESULT", message: "当前版本已变化，请重新读取任务。"}}})
        : route.fulfill({json: options.retryData ?? {...response, status: "pending", items: []}});
      return route.fulfill({status: 500, json: {error: {message: "Unexpected mutation"}}});
    }
    if (url.pathname.endsWith("/claim-prechecks")) {
      reads.push(url.search);
      if (options.readError) return route.fulfill({status: 503, json: {error: {message: "读取服务暂不可用"}}});
      const data = options.polling && reads.length === 1 ? {...response, status: "running", items: []} : options.data ?? response;
      return route.fulfill({json: data});
    }
    if (url.pathname === "/api/standards") return route.fulfill({json: []});
    if (url.pathname.endsWith("/review-items")) return route.fulfill({json: {document_version: 2, items: [], table_items: [], extraction: []}});
    if (url.pathname.endsWith("/issues")) return route.fulfill({json: options.issues ?? [issue, {...issue, issue_id: "CLAIM-UNKNOWN:2", claim_id: "claim-B"}]} );
    if (url.pathname === `/api/audits/${task.id}`) return route.fulfill({json: {...task, ...options.task}});
    return route.fulfill({status: 404, json: {error: {message: "Unexpected read"}}});
  });
  await page.goto(`/audits/${task.id}/results`);
  return {mutations, reads};
}

test("binds duplicate claim text by ID, keeps opinion independent, locates and survives refresh without writes", async ({page}) => {
  const calls = await fixture(page);
  const panel = page.getByRole("region", {name: "模型初审", exact: true});
  await expect(panel.getByText("材料不足", {exact: true})).toBeVisible();
  await expect(panel.getByText(item.reason, {exact: true})).toBeVisible();
  await expect(panel.getByText(item.missing_materials[0], {exact: true})).toBeVisible();
  await expect(page.locator(".detail-panel").getByText("待复核", {exact: true})).toBeVisible();
  await expect(page.getByText("计算过程", {exact: true})).toHaveCount(0);
  await expect(page.getByRole("button", {name: "重试本批次初审"})).toHaveCount(0);
  await panel.getByRole("button", {name: "定位原图依据 1", exact: true}).click();
  await expect(page.locator('.viewer rect.box-active[x="60"][y="120"]')).toHaveCount(1);
  await page.locator(".issue-row").nth(1).click();
  await expect(panel.getByText("第二处声称独立意见。", {exact: true})).toBeVisible();
  await expect(panel.getByText(item.reason, {exact: true})).toHaveCount(0);
  await panel.locator("summary").filter({hasText: "初审追溯信息"}).click();
  await expect(panel.getByText("gpt-6.1-sol / gpt-6.1-sol", {exact: true})).toBeVisible();
  await page.screenshot({path: "test-results/claim-precheck-desktop.png", fullPage: true});
  await page.setViewportSize({width: 375, height: 812});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.screenshot({path: "test-results/claim-precheck-mobile.png", fullPage: true});
  await page.reload();
  await expect(panel.getByText("材料不足", {exact: true})).toBeVisible();
  expect(calls.reads.every(search => search === "?version=2")).toBe(true);
  expect(calls.mutations).toEqual([]);
});

test("polls an existing running batch until completion without posting", async ({page}) => {
  const calls = await fixture(page, {polling: true});
  await expect(page.getByRole("status")).toContainText("模型正在初审");
  await expect(page.getByRole("region", {name: "模型初审", exact: true}).getByText("材料不足", {exact: true})).toBeVisible({timeout: 8000});
  expect(calls.reads.length).toBeGreaterThan(1);
  expect(calls.mutations).toEqual([]);
});

test("only explicit unavailable retry posts the batch version and then polls", async ({page}) => {
  const calls = await fixture(page, {data: {...response, status: "unavailable", items: failedItems, error_code: "QUOTA", error_message: "初审额度已用尽"}});
  const panel = page.getByRole("region", {name: "模型初审", exact: true});
  await expect(panel.getByText("此项初审暂不可用", {exact: true})).toBeVisible();
  await expect(panel.getByText("材料不足", {exact: true})).toHaveCount(0);
  expect(calls.mutations).toEqual([]);
  await panel.getByRole("button", {name: "重试失败项（2）", exact: true}).click();
  await expect(panel.getByRole("status")).toContainText("已排队");
  expect(calls.mutations).toEqual(["POST /api/audits/claim-contract/claim-prechecks/retry?version=2"]);
});

for (const mode of ["stale", "wrong-version", "wrong-id", "missing-id"]) test(`fails closed on ${mode}`, async ({page}) => {
  const data = mode === "stale" ? {...response, stale: true} : mode === "wrong-version" ? {...response, document_version: 1} : mode === "missing-id" ? response : {...response, items: [{...item, claim_id: "unrelated"}]};
  const calls = await fixture(page, {data, issues: [{...issue, claim_id: mode === "missing-id" ? null : issue.claim_id}]});
  const panel = page.getByRole("region", {name: "模型初审", exact: true});
  await expect(panel).toContainText(mode === "missing-id" ? "缺少声称关联" : mode === "wrong-id" ? "没有可匹配" : "已过期");
  await expect(panel.getByText(item.reason, {exact: true})).toHaveCount(0);
  await expect(panel.getByRole("button", {name: /重试失败项/})).toHaveCount(0);
  if (mode === "missing-id") {
    await panel.getByRole("button", {name: /^第2项 ·/}).click();
    await expect(panel.getByText("第二处声称独立意见。", {exact: true})).toBeVisible();
    await expect(panel.getByText(item.reason, {exact: true})).toHaveCount(0);
  }
  expect(calls.mutations).toEqual([]);
});

test("read failures offer a GET reload and retry conflicts do not alter decisions", async ({page}) => {
  const calls = await fixture(page, {readError: true});
  const panel = page.getByRole("region", {name: "模型初审", exact: true});
  await panel.getByRole("button", {name: "重新读取", exact: true}).click();
  await expect.poll(() => calls.reads.length).toBeGreaterThan(1);
  expect(calls.mutations).toEqual([]);
});

test("explicit retry conflict is displayed without writing an issue decision", async ({page}) => {
  const calls = await fixture(page, {data: {...response, status: "unavailable", items: failedItems}, retryError: true});
  const panel = page.getByRole("region", {name: "模型初审", exact: true});
  await panel.getByRole("button", {name: "重试失败项（2）", exact: true}).click();
  await expect(panel.getByText("初审重试未成功", {exact: true})).toBeVisible();
  await expect(panel.getByText("当前版本已变化，请重新读取任务。", {exact: true})).toBeVisible();
  expect(calls.mutations).toHaveLength(1);
  await expect(page.locator(".detail-panel").getByText("处理状态：尚未处理", {exact: true})).toBeVisible();
});

for (const [verdict, label] of [["supported", "有材料支持"], ["contradiction", "发现矛盾"]]) test(`renders ${verdict} and source citations without changing the rule conclusion`, async ({page}) => {
  const citation = {id: "reference-1", title: "经审核的参考资料", source_type: "internal_guidance", file: "reference.md", section: "工艺核查", excerpt: "用于测试引用展示的已审核片段。", sha256: "b".repeat(64), scope_keywords: ["工艺"], source_verification: "source_verified", can_support_verdict: true, review: {reviewer: "测试审核人"}};
  const calls = await fixture(page, {data: {...response, items: [{...item, verdict, reason: "根据引用材料给出的独立初审意见。", reference_citations: [citation], missing_materials: []}]}});
  const panel = page.getByRole("region", {name: "模型初审", exact: true});
  await expect(panel.getByText(label!, {exact: true})).toBeVisible();
  await expect(panel.getByText(citation.excerpt, {exact: true})).toBeVisible();
  await panel.getByText("来源与适用范围", {exact: true}).click();
  await expect(panel.getByText("reference.md · 工艺核查", {exact: true})).toBeVisible();
  await expect(page.locator(".detail-panel").getByText("待复核", {exact: true})).toBeVisible();
  await expect(page.locator(".detail-panel").getByText("处理状态：尚未处理", {exact: true})).toBeVisible();
  expect(calls.mutations).toEqual([]);
});

test("existing completed live opinions display read-only without model retries", async ({page, request}) => {
  const tasks = await (await request.get("/api/audits?limit=50")).json();
  let found: {task: any; issue: any; item: any} | undefined;
  for (const current of tasks.items) {
    if (current.check_doc_version !== current.document_version) continue;
    const read = await request.get(`/api/audits/${current.id}/claim-prechecks?version=${current.document_version}`);
    if (!read.ok()) continue;
    const data = await read.json();
    if (!["completed", "partial"].includes(data.status) || data.stale || !data.items.length) continue;
    const issues = await (await request.get(`/api/audits/${current.id}/issues`)).json();
    const bound = issues.find((entry: any) => entry.rule_id === "CLAIM-UNKNOWN" && (!entry.claim_id || data.items.some((opinion: any) => opinion.claim_id === entry.claim_id && opinion.status === "completed")));
    if (bound) { found = {task: current, issue: bound, item: bound.claim_id ? data.items.find((opinion: any) => opinion.claim_id === bound.claim_id) : data.items[0]}; break; }
  }
  test.skip(!found, "Requires an existing completed claim batch; never creates or retries one");
  const writes: string[] = [];
  await page.route("**/api/**", route => {
    if (route.request().method() === "GET") return route.continue();
    writes.push(route.request().url());
    return route.fulfill({status: 500, json: {error: {message: "Live verification is read-only"}}});
  });
  await page.goto(`/audits/${found!.task.id}/results`);
  // Stable issue order is the backend status ordering, not claim text matching.
  const issues = await (await request.get(`/api/audits/${found!.task.id}/issues`)).json();
  const order: Record<string, number> = {FAIL: 0, REVIEW: 1, NOT_CHECKED: 2, PASS: 3};
  issues.sort((a: any, b: any) => order[a.status] - order[b.status]);
  await page.locator(".issue-row").nth(issues.findIndex((entry: any) => entry.issue_id === found!.issue.issue_id)).click();
  const panel = page.getByRole("region", {name: "模型初审", exact: true});
  if (!found!.issue.claim_id) {
    await expect(panel.getByText("当前检查项缺少声称关联", {exact: true})).toBeVisible();
    await panel.getByRole("button", {name: /^第1项 ·/}).click();
  }
  await expect(panel.getByText(found!.item.claim_text, {exact: false}).first()).toBeVisible();
  await expect(panel.getByText(found!.item.reason, {exact: true})).toBeVisible();
  await page.screenshot({path: "test-results/live-claim-precheck.png", fullPage: true});
  expect(writes).toEqual([]);
});

test("partial success shows valid opinions and isolates the rejected claim", async ({page}) => {
  const calls = await fixture(page, {data: partial});
  const panel = page.getByRole("region", {name: "模型初审", exact: true});
  await expect(panel.getByText("已完成 1/2 项初审", {exact: true})).toBeVisible();
  await expect(panel.getByText("材料不足", {exact: true})).toBeVisible();
  await expect(panel.getByText(item.reason, {exact: true})).toBeVisible();
  await expect(panel.getByText("已从保存的模型响应恢复意见，本次未新增模型调用。", {exact: true})).toBeVisible();
  await page.locator(".issue-row").nth(1).click();
  await expect(panel.getByText("此项初审暂不可用", {exact: true})).toBeVisible();
  await expect(panel.getByText(partial.items[1].error_message!, {exact: true})).toBeVisible();
  await expect(panel.getByText("发现矛盾", {exact: true})).toHaveCount(0);
  await expect(panel.getByRole("button", {name: "重试失败项（1）", exact: true})).toBeVisible();
  expect(calls.mutations).toEqual([]);
});

test("retry keeps completed opinions visible while failed items are pending", async ({page}) => {
  const retryData = {...partial, status: "pending", items: [item, {...failedItems[1], status: "pending"}]};
  const calls = await fixture(page, {data: partial, retryData});
  const panel = page.getByRole("region", {name: "模型初审", exact: true});
  await panel.getByRole("button", {name: "重试失败项（1）", exact: true}).click();
  await expect(panel.getByRole("status")).toContainText("已排队重试");
  await expect(panel.getByText(item.reason, {exact: true})).toBeVisible();
  await expect(panel.getByRole("button", {name: /重试失败项/})).toHaveCount(0);
  expect(calls.mutations).toEqual(["POST /api/audits/claim-contract/claim-prechecks/retry?version=2"]);
});

test("partial results fail closed when the batch becomes stale", async ({page}) => {
  const calls = await fixture(page, {data: {...partial, stale: true}});
  const panel = page.getByRole("region", {name: "模型初审", exact: true});
  await expect(panel.getByText("初审意见已过期", {exact: true})).toBeVisible();
  await expect(panel.getByText(item.reason, {exact: true})).toHaveCount(0);
  await expect(panel.getByRole("button", {name: /重试失败项/})).toHaveCount(0);
  expect(calls.mutations).toEqual([]);
});
