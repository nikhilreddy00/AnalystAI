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
    // Recharts measures its container after layout, at its own pace: its contents are render
    // state, not the report (chart titles, notes and rationale stay in the comparison).
    c.querySelectorAll(".recharts-responsive-container").forEach((n) => n.replaceChildren());
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
  const ms = Date.now() - t0;
  console.log(`sidebar drawer with 201 threads rendered in ${ms} ms`);
  expect(ms).toBeLessThan(1000);   // the drawer, in a dev build; the list API is p95 5 ms
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

// ── Independent review #2: each finding, as a test that failed before its fix ─────────────────

const sidebar = (page: Page) => page.getByRole("complementary", { name: "Analyses sidebar" });
const openRow = (page: Page, text: string) =>
  sidebar(page).getByTestId("thread-row").filter({ hasText: text }).getByRole("button").first().click();

test("B1: a running thread reopened quickly (A, B, A) keeps its question and report", async ({ page }) => {
  writeFileSync(SCENARIO, "trickle\n");
  await page.request.post(`${API}/__seed?n=1`);
  await page.route(`${API}/api/sessions/ui-check/thread`, async (route) => {
    const body = await (await route.fetch()).json();
    body.turns = [{ ...body.turns[0], status: "running", report: [], report_sha256: null }];
    await route.fulfill({ json: body });
  });
  await page.route(`${API}/api/sessions/t-0/thread`, async (route) => {
    await new Promise((r) => setTimeout(r, 300));
    await route.continue();
  });
  await page.goto("/chat?t=ui-check");
  await page.waitForTimeout(2500);   // mid-stream
  await openRow(page, "question 0");
  await openRow(page, QUESTION);
  const chat = page.getByRole("region", { name: "Chat" });
  await expect(chat.getByText(QUESTION)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(SUMMARY).first()).toBeVisible({ timeout: 30_000 });
});

test("B2: a question still being posted doesn't take over the thread opened next", async ({ page }) => {
  await page.request.post(`${API}/__seed?n=1`);
  await page.goto("/chat?t=ui-check");
  await expect(page.getByText(SUMMARY).first()).toBeVisible({ timeout: 30_000 });
  let release!: () => void;
  const held = new Promise<void>((r) => (release = r));
  await page.route(`${API}/chat`, async (route) => {
    await held;
    await route.continue();
  });
  const input = page.getByLabel("Ask a question about your data");
  await input.fill("a follow-up in ui-check");
  await input.press("Enter");
  await openRow(page, "question 0");
  await expect(page).toHaveURL(/t=t-0/);
  release();
  await page.waitForTimeout(2000);
  await expect(page).toHaveURL(/t=t-0/);
  await expect(page.getByRole("region", { name: "Chat" }).getByText("a follow-up in ui-check")).toHaveCount(0);
});

test("B3: an earlier upload's data never rides along on a reopened thread's follow-up", async ({ page }) => {
  await page.request.post(`${API}/__seed?n=1`);
  await page.goto("/chat");
  await page.locator('input[type="file"]').setInputFiles({ name: "other.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n") });
  await expect(page.getByLabel("Ask a question about your data")).toBeEnabled();
  await openRow(page, "question 0");
  await expect(page.getByText(SUMMARY).first()).toBeVisible({ timeout: 30_000 });
  const posted = page.waitForRequest((r) => r.url().endsWith("/chat") && r.method() === "POST");
  const input = page.getByLabel("Ask a question about your data");
  await input.fill("follow-up on t-0");
  await input.press("Enter");
  const body = JSON.parse((await posted).postData() ?? "{}");
  expect(body.session_id).toBe("t-0");
  expect(body.dataset_version_id).toBeUndefined();
  expect(body.dataset_id).toBeUndefined();
});

test("M5: a failed run with a partial report reopens with its error and retry", async ({ page }) => {
  await page.route(`${API}/api/sessions/ui-check/thread`, async (route) => {
    const body = await (await route.fetch()).json();
    const t = body.turns[0];
    body.turns = [{ ...t, status: "failed", error: "The insight step failed.",
      report: t.report.filter((e: { type: string }) => ["briefing", "evidence", "charts"].includes(e.type)) }];
    await route.fulfill({ json: body });
  });
  await page.goto("/chat?t=ui-check");
  const chat = page.getByRole("region", { name: "Chat" });
  await expect(chat.getByText("The insight step failed.")).toBeVisible({ timeout: 30_000 });
  await expect(chat.getByRole("button", { name: "Retry from where it stopped" })).toBeVisible();
});

test("m5: a retry in a reopened thread streams on after its last event, not from the start", async ({ page }) => {
  await page.route(`${API}/api/sessions/ui-check/thread`, async (route) => {
    const body = await (await route.fetch()).json();
    body.turns = [{ ...body.turns[0], status: "failed", error: "Stopped." }];
    await route.fulfill({ json: body });
  });
  await page.goto("/chat?t=ui-check");
  const retry = page.getByRole("button", { name: "Retry from where it stopped" });
  await expect(retry).toBeVisible({ timeout: 30_000 });
  const events = page.waitForRequest((r) => r.url().endsWith("/runs/run-1/events"));
  await retry.click();
  const n = (await (await page.request.get(`${API}/api/sessions/ui-check/thread`)).json()).turns[0].last_seq;
  expect((await events).headers()["last-event-id"]).toBe(String(n));
});

test("m3: back to plain /chat leaves the thread", async ({ page }) => {
  await page.request.post(`${API}/__seed?n=1`);
  await page.goto("/chat");
  await openRow(page, QUESTION);
  await expect(page.getByText(SUMMARY).first()).toBeVisible({ timeout: 30_000 });
  await page.goBack();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByText("Connect your data").first()).toBeVisible();
});

test("M6: asking a question refreshes the sidebar while it runs (its running dot)", async ({ page }) => {
  await page.goto("/chat?t=ui-check");
  await expect(page.getByText(SUMMARY).first()).toBeVisible({ timeout: 30_000 });
  writeFileSync(SCENARIO, "trickle\n");   // a run that lasts: the refresh must come before it ends
  const posted = page.waitForResponse((r) => r.url().endsWith("/chat") && r.request().method() === "POST");
  const input = page.getByLabel("Ask a question about your data");
  await input.fill("another question");
  await input.press("Enter");
  await posted;   // the run exists now: the list must be read again, not at the end of the run
  await page.waitForRequest((r) => r.url().endsWith("/api/sessions") && r.method() === "GET",
                            { timeout: 3000 });
  await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();   // still running
});

test("m7: after a rename, focus is back on the thread", async ({ page }) => {
  await page.goto("/chat");
  const row = sidebar(page).getByTestId("thread-row").first();
  await row.getByRole("button", { name: /^Actions for/ }).click();
  await expect(page.getByRole("menuitem", { name: "Rename" })).toBeFocused();   // menu opens on its first item
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "Pin" })).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Enter");
  await sidebar(page).getByLabel("Thread title").fill("Q4 review");
  await page.keyboard.press("Enter");
  await expect(row.getByRole("button").first()).toBeFocused();
});

test("C2: a new thread's first question, then another thread opened before both answer", async ({ page }) => {
  await page.request.post(`${API}/__seed?n=1`);
  await page.goto("/chat");
  await page.locator('input[type="file"]').setInputFiles({ name: "sales_nov.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n") });
  let releasePost!: () => void;
  const post = new Promise<void>((r) => (releasePost = r));
  await page.route(`${API}/chat`, async (route) => {
    await post;
    await route.continue();
  });
  let releaseThread!: () => void;
  const thread = new Promise<void>((r) => (releaseThread = r));
  await page.route(`${API}/api/sessions/t-0/thread`, async (route) => {
    await thread;
    await route.continue();
  });
  const input = page.getByLabel("Ask a question about your data");
  await expect(input).toBeEnabled();
  await input.fill("first question of a new thread");
  await input.press("Enter");
  await openRow(page, "question 0");
  releasePost();   // the new thread's POST answers while t-0 is still loading
  await page.waitForTimeout(1000);
  releaseThread();
  await expect(page.getByText(SUMMARY).first()).toBeVisible({ timeout: 30_000 });
  await expect(page).toHaveURL(/t=t-0/);
  await expect(page.getByRole("region", { name: "Chat" }).getByText("first question of a new thread")).toHaveCount(0);
});
