// Dashboard filters (user report, 2026-11-03): filtering changed nothing on the charts. The page
// now shows the server's recomputation for the filters (mock: scripts/ui_check/mock_api.py
// `filtered`), and a chart redraws with its new data (ChartRenderer no longer freezes its props).
import { expect, test } from "@playwright/test";

const API = "http://127.0.0.1:8100";

test.beforeEach(async ({ page }) => {
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

test("a filter redraws a mean chart with the filtered data (1440 px)", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/dashboard");
  const chart = page.locator("figure").filter({ hasText: "Average Revenue by Segment" }).first();
  await expect(chart).toBeVisible({ timeout: 30_000 });
  const ticks = chart.locator(".recharts-cartesian-axis-tick-value");
  await expect(ticks.filter({ hasText: /^A$/ })).toHaveCount(1);
  await page.locator("#dashboard-filters").getByText("B", { exact: true }).first().click();
  await expect(ticks.filter({ hasText: /^A$/ })).toHaveCount(0, { timeout: 10_000 });
  await expect(ticks.filter({ hasText: /^B$/ })).toHaveCount(1);
  const card = chart.locator("xpath=..");
  await expect(card.getByText("shows unfiltered data")).toHaveCount(0);
});
