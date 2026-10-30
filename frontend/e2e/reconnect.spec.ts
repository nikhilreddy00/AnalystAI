// Phase 5.6a "Done when": a dropped stream or a closed-and-reopened tab replays every event of a
// run in order, once. The mock (scripts/ui_check/mock_api.py) logs every seq it sends.
import { expect, test, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";

const SCENARIO = "../scripts/ui_check/fixtures/scenario";
const API = "http://127.0.0.1:8100";
const SUMMARY = "Segment B's revenue is falling";
const QUESTION = "Give me an overview of this sales data";
const N_EVENTS = readFileSync("../scripts/ui_check/fixtures/full.sse", "utf8")
  .split("\n\n")
  .filter((c) => c.trim().startsWith("data: ")).length;
const all = Array.from({ length: N_EVENTS }, (_, i) => i + 1);

test.afterEach(() => writeFileSync(SCENARIO, "full\n"));

async function signIn(page: Page) {
  await page.addInitScript(() => {
    const session = {
      access_token: "t", refresh_token: "r", token_type: "bearer", expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      user: { id: "u1", email: "demo@example.com", aud: "authenticated", user_metadata: {} },
    };
    localStorage.setItem("sb-127-auth-token", JSON.stringify(session));
  });
}

async function startRun(page: Page) {
  await page.goto("/chat");
  await page.locator('input[type="file"]').setInputFiles({
    name: "sales_nov.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n"),
  });
  const input = page.getByLabel("Ask a question about your data");
  await expect(input).toBeEnabled();
  await input.fill(QUESTION);
  await input.press("Enter");
}

async function served(page: Page): Promise<number[][]> {
  return (await page.request.get(`${API}/__served`)).json();
}

async function expectOneReport(page: Page) {
  await expect(page.getByText(SUMMARY).first()).toBeVisible({ timeout: 30_000 });
  // Chat bubbles only (the question is also the report's heading): one question, one answer.
  await expect(page.locator("div.ml-auto.rounded-lg", { hasText: QUESTION })).toHaveCount(1);
  await expect(page.locator("p.line-clamp-\\[8\\]", { hasText: SUMMARY })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0);  // the run is over
}

test("a dropped stream reconnects with Last-Event-ID and misses or repeats nothing", async ({ page }) => {
  writeFileSync(SCENARIO, "drop\n");
  await signIn(page);
  await startRun(page);
  await expectOneReport(page);
  const conns = await served(page);
  expect(conns.length).toBeGreaterThanOrEqual(2);       // the first connection was cut
  expect(conns.flat()).toEqual(all);                    // across connections: each seq once, in order
});

test("a tab closed mid-run and reopened replays the whole run once", async ({ context }) => {
  writeFileSync(SCENARIO, "trickle\n");
  const page = await context.newPage();
  await signIn(page);
  await startRun(page);
  await expect.poll(async () => (await served(page)).flat().length).toBeGreaterThan(5);
  await page.close();                                   // mid-run

  const reopened = await context.newPage();             // same browser profile: localStorage kept
  await signIn(reopened);
  await reopened.goto("/chat");
  await expectOneReport(reopened);
  const conns = await served(reopened);
  expect(conns[conns.length - 1]).toEqual(all);         // the reopened tab: every event, in order, once
});
