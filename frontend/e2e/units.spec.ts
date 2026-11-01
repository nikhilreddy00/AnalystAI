// Phase 5.5: a column's unit comes from the backend (semantic layer or the file), never from its
// name. Against the mock backend (scripts/ui_check; no keys, $0): the `units` run's briefing
// says revenue is EUR and marketing_spend is °C; a column without a unit keeps the old name rule.
import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";

const SCENARIO = "../scripts/ui_check/fixtures/scenario";
const API = "http://127.0.0.1:8100";

test.beforeEach(async ({ page }) => {
  writeFileSync(SCENARIO, "units\n");
  await page.request.post(`${API}/__reset`);
  await page.addInitScript(() => {
    const session = {
      access_token: "t", refresh_token: "r", token_type: "bearer", expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      user: { id: "u1", email: "demo@example.com", aud: "authenticated", user_metadata: {} },
    };
    localStorage.setItem("sb-127-auth-token", JSON.stringify(session));
  });
});

test.afterEach(() => writeFileSync(SCENARIO, "full\n"));

test("descriptive statistics show each column in its own unit", async ({ page }) => {
  await page.goto("/chat");
  await page.locator('input[type="file"]').setInputFiles({
    name: "sales_nov.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n"),
  });
  const input = page.getByLabel("Ask a question about your data");
  await expect(input).toBeEnabled();
  await input.fill("Give me an overview of this sales data");
  await input.press("Enter");
  await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0, { timeout: 30_000 });
  await expect(page.getByText("€102.82").first()).toBeVisible();   // the KPI tile, Summary tab
  await page.getByRole("tab", { name: /^Data/ }).click();
  const table = page.locator("table", { hasText: "Skewness" });
  const row = (col: string) => table.locator("tr", { hasText: col }).first();
  await expect(row("revenue")).toContainText("€102.82");       // EUR, not the name rule's $
  await expect(row("revenue")).not.toContainText("$");
  await expect(row("marketing_spend")).toContainText("°C");      // a unit after the number
  await expect(row("marketing_spend")).not.toContainText("$");
});

test("a column without a unit keeps the old name rule", async ({ page }) => {
  writeFileSync(SCENARIO, "full\n");   // the run before Phase 5.5: no column_units at all
  await page.goto("/chat");
  await page.locator('input[type="file"]').setInputFiles({
    name: "sales_nov.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n"),
  });
  const input = page.getByLabel("Ask a question about your data");
  await expect(input).toBeEnabled();
  await input.fill("Give me an overview of this sales data");
  await input.press("Enter");
  await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0, { timeout: 30_000 });
  await page.getByRole("tab", { name: /^Data/ }).click();
  const table = page.locator("table", { hasText: "Skewness" });
  await expect(table.locator("tr", { hasText: "revenue" }).first()).toContainText("$102.82");
});

test("a final error offers no retry, live or reopened", async ({ page }) => {
  writeFileSync(SCENARIO, "final\n");
  await page.goto("/chat");
  await page.locator('input[type="file"]').setInputFiles({
    name: "sales_nov.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n"),
  });
  const input = page.getByLabel("Ask a question about your data");
  await expect(input).toBeEnabled();
  await input.fill("Give me an overview of this sales data");
  await input.press("Enter");
  const alert = page.getByRole("alert").filter({ hasText: "Upload it again" });
  await expect(alert).toBeVisible({ timeout: 30_000 });
  await expect(alert.getByRole("button")).toHaveCount(0);
  await page.reload();   // reopened from the stored thread
  await expect(alert).toBeVisible({ timeout: 30_000 });
  await expect(alert.getByRole("button")).toHaveCount(0);
});

test("data that changed offers 'Try again' but not a resume", async ({ page }) => {
  writeFileSync(SCENARIO, "changed\n");
  await page.goto("/chat");
  await page.locator('input[type="file"]').setInputFiles({
    name: "sales_nov.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n"),
  });
  const input = page.getByLabel("Ask a question about your data");
  await expect(input).toBeEnabled();
  await input.fill("Give me an overview of this sales data");
  await input.press("Enter");
  const alert = page.getByRole("alert").filter({ hasText: "Ask the question again" });
  await expect(alert).toBeVisible({ timeout: 30_000 });
  await expect(alert.getByRole("button", { name: "Try again" })).toBeVisible();
  await page.reload();
  await expect(alert.getByRole("button", { name: "Try again" })).toBeVisible({ timeout: 30_000 });
  await expect(alert.getByRole("button", { name: /Retry from where/ })).toHaveCount(0);
});
