import { test, expect } from "@playwright/test";

test("local API rejects cross-origin writes and exposes no login requirement", async ({request}) => {
  expect((await request.get("/api/standards")).status()).toBe(200);
  const response = await request.post("/api/audits", {headers: {Origin: "https://untrusted.example"}, multipart: {source: "upload"}});
  expect(response.status()).toBe(403);
  expect((await response.json()).error.code).toBe("ORIGIN_REJECTED");
  const schema = await (await request.get("/openapi.json")).json();
  expect(schema.paths["/api/auth/login"]).toBeUndefined();
  expect(schema.paths["/api/audits"].get.security).toBeUndefined();
});
