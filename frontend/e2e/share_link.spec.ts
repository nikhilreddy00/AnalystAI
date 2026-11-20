// Phase 5.8b at 1440 px ($0): a share link pasted under the upload is sent to /connectors/link as
// typed and becomes the data of the analysis, as an upload would; a refusal is shown as the
// server says it (the server names only the host, never the link's token:
// tests/test_share_links.py). The live check imports a real public Google Sheet.
import { expect, test, type Page } from "@playwright/test";

const LINK = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjK/edit?usp=sharing#gid=0";

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

test("a share link is imported as a dataset", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page);
  const sent: Record<string, unknown>[] = [];
  await page.route("**/connectors/link", async (route) => {
    sent.push(route.request().postDataJSON());
    await route.fulfill({ json: {
      status: "new_dataset", filename: "Q3 orders - Sheet1.csv", dataset_id: "d1",
      dataset_version_id: "v1", version: 1, dataset_name: "Q3 orders - Sheet1", file_hash: "h",
      dataset: { id: "d1", name: "Q3 orders - Sheet1" } } });
  });
  await page.goto("/chat");
  await page.getByLabel("Or a share link").fill(LINK);
  await page.getByRole("button", { name: "Import" }).click();
  await expect(page.getByText("Q3 orders - Sheet1").first()).toBeVisible();
  expect(sent).toEqual([{ url: LINK }]);
});

test("a link that isn't public says why", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page);
  const detail = "The link to docs.google.com didn't give a file (a web page came back, not a " +
    "file): it may not be public. Share it as 'Anyone with the link', and link to a CSV or Excel file.";
  await page.route("**/connectors/link", (route) => route.fulfill({ status: 400, json: { detail } }));
  await page.goto("/chat");
  await page.getByLabel("Or a share link").fill(LINK);
  await page.getByRole("button", { name: "Import" }).click();
  await expect(page.getByRole("status")).toContainText("it may not be public");
});
