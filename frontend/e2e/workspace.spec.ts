// Phase 5.6b workspace, against the mock backend (scripts/ui_check/mock_api.py; no keys, $0):
// a reopened report renders as the live one did, the sidebar (200 threads, 390 px phone width,
// search, rename / pin / archive / delete with confirmation), stale reports, the Datasets view.
import { expect, test, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";

const SCENARIO = "../scripts/ui_check/fixtures/scenario";
const API = "http://127.0.0.1:8100";
const SUMMARY = "Segment B's revenue is falling";
const QUESTION = "Give me an overview of this sales data";
const PHONE = { width: 390, height: 844 };

test.beforeEach(async ({ page }) => {
  writeFileSync(SCENARIO, "full\n");
  await page.request.post(`${API}/__reset`);
  await page.addInitScript(() => {
    const session = {
      access_token: "t", refresh_token: "r", token_type: "bearer", expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      user: { id: "u1", email: "demo@example.com", aud: "authenticated", user_metadata: {} },
    };
    localStorage.setItem("sb-127-auth-token", JSON.stringify(session));
    // Recharts animates: the DOM comparison reads settled charts.
    const style = document.createElement("style");
    style.textContent = "*{animation:none!important;transition:none!important}";
    document.addEventListener("DOMContentLoaded", () => document.head.appendChild(style));
  });
});

/** The report canvas's content, minus what differs by render and not by report: React ids,
 *  SVG geometry, and the provenance line only a stored report has. */
async function reportDom(page: Page, tab: string): Promise<string> {
  await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
  return page.locator("article").evaluate((el) => {
    const c = el.cloneNode(true) as HTMLElement;
    c.querySelectorAll("svg, [data-testid=provenance], header button").forEach((n) => n.remove());
    c.querySelectorAll("*").forEach((n) => {
      for (const a of [...n.attributes]) if (a.name === "id" || a.name.startsWith("aria-")) n.removeAttribute(a.name);
    });
    return c.innerHTML;
  });
}

async function noSideScroll(page: Page) {
  const [scroll, width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(scroll).toBeLessThanOrEqual(width);
}

test("a reopened thread renders the report exactly as it was delivered", async ({ page }) => {
  await page.goto("/chat");
  await page.locator('input[type="file"]').setInputFiles({
    name: "sales_nov.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n"),
  });
  const input = page.getByLabel("Ask a question about your data");
  await expect(input).toBeEnabled();
  await input.fill(QUESTION);
  await input.press("Enter");
  await expect(page.getByText(SUMMARY).first()).toBeVisible({ timeout: 30_000 });
  await expect(page).toHaveURL(/\/chat\?t=ui-check$/);   // the thread has its own address
  await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0);

  const tabs = ["Summary", "Evidence", "Data", "Review"];
  const live: string[] = [];
  for (const t of tabs) live.push(await reportDom(page, t));

  await page.reload();   // the run is over: the thread loads from its stored report events
  await expect(page.getByText(SUMMARY).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("provenance")).toContainText("7 model calls");
  for (const [i, t] of tabs.entries()) expect(await reportDom(page, t), t).toBe(live[i]);
  await expect(page.getByLabel("Ask a question about your data")).toBeEnabled();   // follow-ups
});

test("the sidebar lists 200 threads at phone width", async ({ page }) => {
  await page.request.post(`${API}/__seed?n=200`);
  await page.setViewportSize(PHONE);
  await page.goto("/chat");
  const t0 = Date.now();
  await page.getByRole("button", { name: "Analyses", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "Analyses" });
  await expect(drawer.getByTestId("thread-row")).toHaveCount(201);
  console.log(`sidebar drawer with 201 threads rendered in ${Date.now() - t0} ms`);
  await expect(drawer.getByRole("heading", { name: "Older" })).toBeVisible();
  await noSideScroll(page);

  await drawer.getByPlaceholder("Search titles, questions, datasets").fill("question 170");
  await expect(drawer.getByTestId("thread-row")).toHaveCount(1);
  await drawer.getByPlaceholder("Search titles, questions, datasets").fill("no such thing");
  await expect(drawer.getByText(/No analyses match/)).toBeVisible();
});

test("rename, pin, archive and delete with confirmation", async ({ page }) => {
  await page.goto("/chat");
  const side = page.getByRole("complementary", { name: "Analyses sidebar" });
  const row = () => side.getByTestId("thread-row").first();
  await expect(row()).toContainText("sales_nov · " + QUESTION);
  await expect(row()).toContainText("Done");   // the status, also for screen readers

  await row().getByRole("button", { name: /^Actions for/ }).click();
  await page.getByRole("menuitem", { name: "Rename" }).click();
  await side.getByLabel("Thread title").fill("Board numbers");
  await side.getByLabel("Thread title").press("Enter");
  await expect(row()).toContainText("Board numbers");
  await page.reload();
  await expect(row()).toContainText("Board numbers");   // saved on the server

  await row().getByRole("button", { name: /^Actions for/ }).click();
  await page.getByRole("menuitem", { name: "Pin" }).click();
  await expect(side.getByRole("heading", { name: "Pinned" })).toBeVisible();

  await row().getByRole("button", { name: /^Actions for/ }).click();
  await page.getByRole("menuitem", { name: "Archive" }).click();
  await expect(side.getByText("Archived · 1")).toBeVisible();
  await side.getByText("Archived · 1").click();

  await row().getByRole("button", { name: /^Actions for/ }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  const confirm = page.getByRole("dialog", { name: "Delete this analysis?" });
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(side.getByTestId("thread-row")).toHaveCount(1);
  await row().getByRole("button", { name: /^Actions for/ }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await confirm.getByRole("button", { name: "Delete" }).click();
  await expect(side.getByTestId("thread-row")).toHaveCount(0);
  await expect(side.getByText("No analyses yet")).toBeVisible();
});

test("the open thread, deleted, resets the workspace", async ({ page }) => {
  await page.goto("/chat?t=ui-check");
  await expect(page.getByText(SUMMARY).first()).toBeVisible({ timeout: 30_000 });
  const side = page.getByRole("complementary", { name: "Analyses sidebar" });
  await side.getByTestId("thread-row").first().getByRole("button", { name: /^Actions for/ }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await page.getByRole("dialog", { name: "Delete this analysis?" }).getByRole("button", { name: "Delete" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByText("Connect your data").first()).toBeVisible();
});

test("a deleted or unknown thread link says so", async ({ page }) => {
  await page.goto("/chat?t=gone");
  await expect(page.getByText("That analysis no longer exists")).toBeVisible();
  await expect(page).toHaveURL(/\/chat$/);
});

test("a report on old data says so and offers a re-run", async ({ page }) => {
  await page.request.post(`${API}/__stale`);
  await page.goto("/chat?t=ui-check");
  await expect(page.getByText("The data has changed since this report")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Re-run on current data" })).toBeVisible();
});

test("the desktop sidebar collapses", async ({ page }) => {
  await page.goto("/chat");
  const side = page.getByRole("complementary", { name: "Analyses sidebar" });
  await expect(side).toBeVisible();
  await page.getByRole("button", { name: "Analyses", exact: true }).click();
  await expect(side).toHaveCount(0);
  await page.reload();
  await expect(side).toHaveCount(0);   // remembered
  await page.getByRole("button", { name: "Analyses", exact: true }).click();
  await expect(side).toBeVisible();
});

for (const size of [PHONE, { width: 1280, height: 800 }]) {
  test(`the Datasets view at ${size.width} px`, async ({ page }) => {
    await page.setViewportSize(size);
    await page.goto("/datasets");
    await page.getByTestId("dataset-card").first().click();
    await expect(page.getByRole("heading", { name: "sales_nov" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Versions" })).toContainText("3,000");
    await expect(page.getByRole("link", { name: /sales_nov · / })).toBeVisible();
    await expect(page.getByRole("link", { name: "New analysis" })).toHaveAttribute("href", "/chat?dataset=ds-1");
    await noSideScroll(page);
  });
}
