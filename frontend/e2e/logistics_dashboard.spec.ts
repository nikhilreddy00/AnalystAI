// Domain 8: a TMS shipment export's dashboard at 1440 px. Mock backend (scripts/ui_check; no keys,
// $0): the `tms_shipments` scenario is the real logistics pack output on evals/datagen.py's export
// (`make_fixture.py fixtures/tms_shipments --dataset tms_shipments`). Truth (seed 1): on-time 88.9%
// (open past their promise late, U1); UPS 93.7% on its 1,160 rows.
import { expect, test, type Locator } from "@playwright/test";
import { writeFileSync } from "node:fs";

const SCENARIO = "../scripts/ui_check/fixtures/scenario";
const API = "http://127.0.0.1:8100";

test.beforeEach(async ({ page }) => {
  writeFileSync(SCENARIO, "tms_shipments\n");
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

const paths = (chart: Locator) =>
  chart.locator(".recharts-line-curve").evaluateAll((els) => els.map((e) => e.getAttribute("d")));

test("the logistics overview renders and a carrier filter moves a tile, an off-axis chart and the footer (1440 px)",
  async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/dashboard");
    const overTime = page.locator("figure").filter({ hasText: "On-time delivery over time" }).first();
    const byCarrier = page.locator("figure").filter({ hasText: "On-time delivery by Carrier" }).first();
    await expect(overTime).toBeVisible({ timeout: 30_000 });
    await expect(byCarrier).toBeVisible();
    await expect(byCarrier.locator(".recharts-rectangle")).toHaveCount(5);
    for (const [label, value] of [["On-time delivery", "88.9%"], ["Average transit time", "3.05 days"],
      ["Cost per shipment", "$247.72"], ["Shipments", "2,840"]]) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
      await expect(page.getByText(value, { exact: true }).first()).toBeVisible();
    }
    await page.waitForTimeout(1_600);   // recharts animates its paths for 1.5 s
    const before = await paths(overTime);

    await page.locator("#dashboard-filters").getByText("UPS", { exact: true }).first().click();
    // the server rebuilds the KPIs for the part: a few seconds, 2-3x that on a CI runner
    await expect(page.getByText("Showing 1,160 of 3,000 records")).toBeVisible({ timeout: 45_000 });
    await expect(page.getByText("93.7%", { exact: true }).first()).toBeVisible({ timeout: 20_000 });
    await expect.poll(() => paths(overTime), { timeout: 10_000 }).not.toEqual(before);
    await page.waitForTimeout(1_600);
    await overTime.screenshot({ path: "test-results/logistics-on-time-over-time-1440.png" });
  });
