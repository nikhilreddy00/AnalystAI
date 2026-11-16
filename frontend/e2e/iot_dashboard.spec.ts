// Domain 7: an MES shift report's dashboard at 1440 px. Mock backend (scripts/ui_check; no keys,
// $0): the `shift_production` scenario is the real iot pack output on evals/datagen.py's shift
// report (`make_fixture.py fixtures/shift_production --dataset shift_production`). Truth (seed 1):
// OEE 70.8% pooled over planned minutes; line B 68.7% on its 789 machine-shifts.
import { expect, test, type Locator } from "@playwright/test";
import { writeFileSync } from "node:fs";

const SCENARIO = "../scripts/ui_check/fixtures/scenario";
const API = "http://127.0.0.1:8100";

test.beforeEach(async ({ page }) => {
  writeFileSync(SCENARIO, "shift_production\n");
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

test("the IoT overview renders and a line filter moves a tile, an off-axis chart and the footer (1440 px)",
  async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/dashboard");
    const overTime = page.locator("figure").filter({ hasText: "OEE over time" }).first();
    const byLine = page.locator("figure").filter({ hasText: "OEE by Line" }).first();
    await expect(overTime).toBeVisible({ timeout: 30_000 });
    await expect(byLine).toBeVisible();
    await expect(byLine.locator(".recharts-rectangle")).toHaveCount(3)   // line D: never scheduled, no bar
    for (const [label, value] of [["OEE", "70.8%"], ["Availability", "84.1%"],
      ["Performance rate", "86.6%"], ["MTBF", "42.29 hours"]]) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
      await expect(page.getByText(value, { exact: true }).first()).toBeVisible();
    }
    // a machine id is a pick of machines, never a date range (D7-06)
    await expect(page.locator("#dashboard-filters").getByRole("button", { name: "M01", exact: true }))
      .toBeVisible();
    await page.waitForTimeout(1_600);   // recharts animates its paths for 1.5 s
    const before = await paths(overTime);

    await page.locator("#dashboard-filters").getByText("Line B", { exact: true }).first().click();
    // the server rebuilds the KPIs for the part: a few seconds, 2-3x that on a CI runner
    await expect(page.getByText("Showing 789 of 2,630 records")).toBeVisible({ timeout: 45_000 });
    await expect(page.getByText("68.7%", { exact: true }).first()).toBeVisible({ timeout: 20_000 });
    await expect(overTime.getByText("789 rows", { exact: true })).toBeVisible();   // its footer: the part
    await expect.poll(() => paths(overTime), { timeout: 10_000 }).not.toEqual(before);
    await page.waitForTimeout(1_600);
    await overTime.screenshot({ path: "test-results/iot-oee-over-time-1440.png" });
  });
