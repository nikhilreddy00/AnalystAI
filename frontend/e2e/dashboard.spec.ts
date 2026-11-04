// Dashboard filters (user report, 2026-11-03): filtering changed nothing on the charts. The page
// draws the server's filter options and shows only the server's numbers for the filters (mock:
// scripts/ui_check/mock_api.py `filtered` runs the real filtered_dashboard on the fixture frame);
// the browser never downloads a row or computes a number.
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

test("a filter changes a tile and redraws a chart with the server's numbers (1440 px)", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const rowsFetched: unknown[] = [];
  page.on("response", async (r) => {
    if (r.url().endsWith("/dashboard/latest/data")) rowsFetched.push((await r.json()).rows);
  });
  await page.goto("/dashboard");
  const hist = page.locator("figure").filter({ hasText: "Revenue distribution" }).first();
  await expect(hist).toBeVisible({ timeout: 30_000 });
  const ticks = hist.locator(".recharts-cartesian-axis-tick-value");
  await expect(ticks.first()).toBeVisible();
  const before = await ticks.allTextContents();
  const tile = page.getByText("Total Orders", { exact: true }).locator("xpath=..");
  await expect(tile.getByText(/overall$/)).toHaveCount(0);

  await page.locator("#dashboard-filters").getByText("B", { exact: true }).first().click();

  // the tile: the server's count, against the overall one
  await expect(tile.getByText(/^vs .+ overall$/)).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/^Showing [\d,]+ of [\d,]+ records$/)).toBeVisible();
  // the histogram: rebuilt by the server for segment B (fewer rows per bin)
  await expect.poll(() => ticks.allTextContents(), { timeout: 10_000 }).not.toEqual(before);
  const histCard = hist.locator("xpath=..");
  await expect(histCard.getByText("shows unfiltered data")).toHaveCount(0);
  await expect(histCard.getByText("Updating for the filters…")).toHaveCount(0);
  // a chart from the model's findings can't be rebuilt: it says so instead of pretending
  const mean = page.locator("figure").filter({ hasText: "Average Revenue by Segment" }).first();
  await expect(mean.locator("xpath=..").getByText("shows unfiltered data")).toBeVisible();
  expect(rowsFetched).toEqual([undefined]);   // no row ever reached the browser
});
