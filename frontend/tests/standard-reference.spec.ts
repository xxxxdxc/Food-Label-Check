import { test, expect } from "@playwright/test";

for (const verification of ["pending", "verified"]) {
  test(`shows standard and original link for ${verification}, without clause details`, async ({page}) => {
    const mutations: string[] = [];
    await page.route("**/api/**", route => {
      if (route.request().method() !== "GET") { mutations.push(route.request().method()); return route.abort(); }
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/standards") return route.fulfill({json: []});
      if (path.endsWith("/review-items")) return route.fulfill({json: {document_version: 1, items: [], table_items: [], extraction: []}});
      if (path.endsWith("/issues")) return route.fulfill({json: [{issue_id: "energy", rule_id: "ENERGY-ARITHMETIC", layer: "L1", status: "REVIEW", title: "能量", message: "每份72g能量核对", review_decision: "open", evidence: [], calculation: null, doc_version: 1,
        regulation: {standard: "GB 28050", edition: "2025", clause_id: verification === "verified" ? "2.3" : null, verification_status: verification, note: "旧条款说明", source_title: "食品安全国家标准 预包装食品营养标签通则", source_url: "https://hncdc.com/upload/1/cms/content/editor/1762393427122.pdf"}}]});
      return route.fulfill({json: {id: "standard-reference", status: "PARTIAL", source: "upload", standard_mode: "gb28050_2025_precheck", channel: null, files: [], document_version: 1, check_doc_version: 1, stale: false, review_pending: 0, summary: null, error: null, warnings: [], created_at: "2026-10-20T00:00:00Z", updated_at: "2026-10-20T00:00:00Z"}});
    });
    await page.goto("/audits/standard-reference/results");
    const reference = page.locator(".regulation");
    await expect(reference).toContainText("GB 28050—2025");
    await expect(reference).toContainText("预包装食品营养标签通则");
    const link = reference.getByRole("link", {name: "查看标准原文"});
    await expect(link).toHaveAttribute("href", /\.pdf$/);
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
    await expect(reference).not.toContainText("条款待确认");
    await expect(reference).not.toContainText("2.3");
    await expect(reference).not.toContainText("旧条款说明");
    expect(mutations).toEqual([]);
  });
}
