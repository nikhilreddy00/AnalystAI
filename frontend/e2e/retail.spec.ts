// Phase 5.7, domain 1 (sales / retail): a retail upload's report shows the domain pack's KPI tiles
// (net sales after refunds, orders counted once, AOV per order) and says how its numbers are
// counted, at phone width and in both themes. Mock backend (scripts/ui_check; no keys, $0): the
// `retail` run is the real pack output on evals/datagen.py retail_lines.
import { expect, test } from "@playwright/test";
import { upload } from "./upload";
import { writeFileSync } from "node:fs";

const SCENARIO = "../scripts/ui_check/fixtures/scenario";
const API = "http://127.0.0.1:8100";

test.beforeEach(async ({ page }) => {
  writeFileSync(SCENARIO, "retail\n");
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

for (const theme of ["light", "dark"] as const) {
  test(`retail tiles and how the numbers are counted (390 px, ${theme})`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: theme });
    await page.addInitScript((t) => localStorage.setItem("theme", t), theme);
    await page.goto("/chat");
    const input = await upload(page, "orders_export.csv");
    await input.fill("Give me an overview of this sales data");
    await input.press("Enter");
    await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0, { timeout: 30_000 });
    await page.getByText("Report", { exact: true }).filter({ visible: true }).first().click();   // phone: Chat | Report
    for (const [label, value] of [["Net sales", "$310,635.46"], ["Orders", "2,272"],
                                  ["Avg order value", "$136.72"], ["Return rate", "3.9%"]]) {
      await expect(page.getByText(label, { exact: true }).filter({ visible: true }).first()).toBeVisible();
      await expect(page.getByText(value).filter({ visible: true }).first()).toBeVisible();
    }
    await expect(page.getByText("Total Orders")).toHaveCount(0);   // rows are not orders
    const note = page.getByText("How these numbers are counted");
    await expect(note.filter({ visible: true }).first()).toBeVisible();
    await expect(page.getByText(/Feb 2026 is partial \(12 days of data\)/).filter({ visible: true }).first()).toBeVisible();
    await expect(page.getByText(/rows in currencies other than USD are left out/).filter({ visible: true }).first()).toBeVisible();
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(width).toBeLessThanOrEqual(390);   // no sideways scroll at phone width
  });
}
