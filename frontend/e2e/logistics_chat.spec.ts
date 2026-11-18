// Domain 8 end to end at 1440 px on the mock stack ($0): each scenario replays a real live run's
// events (scripts/ui_check/fixtures/<name>.sse, recorded from the live stack on 2026-11-18) through
// the real chat UI, then the dashboard's filter runs the real filtered_dashboard on that dataset
// (make_fixture.py fixtures/<name> --dataset <name>). Truth: evals/datagen.py, seed 1.
import { expect, test, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { upload } from "./upload";

const SCENARIO = "../scripts/ui_check/fixtures/scenario";
const API = "http://127.0.0.1:8100";

test.afterEach(() => writeFileSync(SCENARIO, "full\n"));

async function login(page: Page) {
  await page.addInitScript(() => {
    const session = {
      access_token: "t", refresh_token: "r", token_type: "bearer", expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      user: { id: "u1", email: "demo@example.com", aud: "authenticated", user_metadata: {} },
    };
    localStorage.setItem("sb-127-auth-token", JSON.stringify(session));
  });
}

const CASES = [
  { name: "tms_shipments", file: "tms_shipments.csv", question: "What is on-time delivery by carrier?",
    tiles: [["On-time delivery", "88.9%"], ["Average transit time", "3.05 days"],
            ["Cost per shipment", "$247.72"], ["Cost per lb", "$0.22"], ["Shipments", "2,840"]],
    filter: "UPS", rows: "Showing 1,160 of 3,000 records", filtered: "93.7%" },
  { name: "order_lines", file: "order_lines.csv", question: "Give me an overview of this dataset.",
    tiles: [["OTIF (orders)", "63.3%"], ["OTIF (lines)", "84.4%"], ["Unit fill rate", "96.7%"],
            ["Line fill rate", "93.2%"], ["Orders", "1,993"]],
    filter: "Reno DC", rows: "Showing 2,639 of 6,025 records", filtered: "72.6%" },
  { name: "dock_appointments", file: "dock_appointments.csv", question: "Give me an overview of this dataset.",
    tiles: [["Average dwell", "2.32 hours"], ["On-time arrival", "72.0%"],
            ["Visits over 2 h at the dock", "35.3%"], ["No-shows", "103"], ["Dock appointments", "1,462"]],
    filter: "Columbus DC", rows: "Showing 368 of 1,500 records", filtered: "4.48 hours" },
];

for (const c of CASES) {
  test(`${c.name}: upload, the report's tiles, then a dashboard filter (1440 px)`, async ({ page }) => {
    writeFileSync(SCENARIO, `${c.name}\n`);
    await page.request.post(`${API}/__reset`);
    await login(page);
    await page.setViewportSize({ width: 1440, height: 900 });

    await page.goto("/chat");
    const input = await upload(page, c.file);
    await input.fill(c.question);
    await input.press("Enter");
    // the run has started and finished: its first tile is drawn (never just "no Stop button yet")
    await expect(page.getByText(c.tiles[0][0], { exact: true }).filter({ visible: true }).first())
      .toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0, { timeout: 60_000 });
    for (const [label, value] of c.tiles) {
      await expect(page.getByText(label, { exact: true }).filter({ visible: true }).first()).toBeVisible();
      await expect(page.getByText(value, { exact: true }).filter({ visible: true }).first()).toBeVisible();
    }
    await page.screenshot({ path: `test-results/logistics-${c.name}-report-1440.png` });

    await page.goto("/dashboard");
    await expect(page.getByText(c.tiles[0][1], { exact: true }).first()).toBeVisible({ timeout: 30_000 });
    await page.locator("#dashboard-filters").getByText(c.filter, { exact: true }).first().click();
    await expect(page.getByText(c.rows)).toBeVisible({ timeout: 45_000 });
    await expect(page.getByText(c.filtered, { exact: true }).first()).toBeVisible({ timeout: 20_000 });
    await page.locator("#dashboard-filters").screenshot({ path: `test-results/logistics-${c.name}-filter-1440.png` });
  });
}
