// 5.7b-3 (PR1-20): a SaaS dashboard shows the MRR bridge and NRR at each month-end, and a segment
// filter redraws both with the server's numbers. Mock backend (scripts/ui_check; no keys, $0):
// the `saas_mrr_snapshots` scenario is the real pack output on evals/datagen.py's MRR snapshots
// (`make_fixture.py fixtures/saas_mrr_snapshots --dataset saas_mrr_snapshots`).
import { expect, test, type Locator } from "@playwright/test";
import { writeFileSync } from "node:fs";

const SCENARIO = "../scripts/ui_check/fixtures/scenario";
const API = "http://127.0.0.1:8100";

test.beforeEach(async ({ page }) => {
  writeFileSync(SCENARIO, "saas_mrr_snapshots\n");
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

const shapes = (chart: Locator, css: string) =>
  chart.locator(css).evaluateAll((els) => els.map((e) => e.getAttribute("d")));

test("the MRR bridge and NRR by month-end follow a segment filter (1440 px)", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/dashboard");
  const bridge = page.locator("figure").filter({ hasText: "MRR bridge, 12 months to" }).first();
  const nrr = page.locator("figure").filter({ hasText: "Net revenue retention over time" }).first();
  await expect(bridge).toBeVisible({ timeout: 30_000 });
  await expect(nrr).toBeVisible();
  for (const step of ["New MRR", "Expansion MRR", "Churned MRR", "MRR moved out"]) {
    await expect(bridge.getByText(step, { exact: true })).toBeVisible();
  }
  // drawn: the five moves (moved in / out are $0 in the whole, so no rectangle)
  await expect.poll(async () => (await shapes(bridge, ".recharts-rectangle")).length,
                    { timeout: 10_000 }).toBe(5);
  await expect.poll(async () => (await shapes(nrr, ".recharts-line-curve")).length).toBe(1);
  await page.waitForTimeout(1_600);   // recharts animates its paths for 1.5 s
  const bars = await shapes(bridge, ".recharts-rectangle");
  const line = await shapes(nrr, ".recharts-line-curve");

  await page.locator("#dashboard-filters").getByText("SMB", { exact: true }).first().click();
  await expect(page.getByText(/^Showing [\d,]+ of [\d,]+ records$/)).toBeVisible({ timeout: 10_000 });
  await expect.poll(() => shapes(bridge, ".recharts-rectangle"), { timeout: 10_000 })
    .not.toEqual(bars);
  await expect.poll(() => shapes(nrr, ".recharts-line-curve"), { timeout: 10_000 }).not.toEqual(line);
  await expect(bridge.locator("xpath=..").getByText("shows unfiltered data")).toHaveCount(0);
  await expect(bridge.getByText(/→ \$60,819\.02/)).toBeVisible();   // the SMB closing MRR
  await page.waitForTimeout(1_600);   // the bars' animation to the filtered values
  await page.screenshot({ path: "test-results/ledger-charts-1440.png", fullPage: true });
});

// PR14-02 (Open): a click on a bar sets no filter on any chart (Recharts 3 passes no activeLabel
// to the chart's onClick). Expected to fail until fixed; the fix must keep a bridge step (no
// column) from becoming a filter, which the second half checks.
test("a click on a segment bar filters by it, a click on a bridge step doesn't", async ({ page }) => {
  test.fail(true, "PR14-02: chart click-to-filter does nothing under Recharts 3");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/dashboard");
  const seg = page.locator("figure").filter({ hasText: "MRR by segment (as of" }).first();
  await expect(seg.locator(".recharts-rectangle").first()).toBeVisible({ timeout: 30_000 });
  await seg.locator(".recharts-rectangle").first().click();
  await expect(page.getByText(/^Showing [\d,]+ of [\d,]+ records$/)).toBeVisible({ timeout: 5_000 });
  await page.getByRole("button", { name: /clear/i }).first().click();
  const bridge = page.locator("figure").filter({ hasText: "MRR bridge, 12 months to" }).first();
  await bridge.locator(".recharts-rectangle").first().click();
  await page.waitForTimeout(1_000);
  await expect(page.getByText(/^Showing /)).toHaveCount(0);
});
