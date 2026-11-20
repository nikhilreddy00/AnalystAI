// Phase 5.8a at 1440 px ($0): the connect form takes a MySQL connection string, sends it to
// /connectors/test as typed, and never shows the password on the page, before or after the
// server answers. The server's own checks (allowlist, read-only, no password in its message) are
// tests/test_connectors_sources.py; the live check connects a real MySQL (docs/baselines/phase5_8a.md).
import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "s3cr3t-pw-5_8a";
const URL = `mysql://readonly:${PASSWORD}@db.example.com:3306/acme`;

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

for (const answer of [
  { success: true, message: "Connection successful" },
  { success: false, message: "Access denied for user 'readonly'@'10.0.0.9' (using password: YES)" },
]) {
  test(`a MySQL connection string is sent as typed and its password never shown (${answer.success ? "connects" : "refused"})`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await login(page);
    const sent: Record<string, unknown>[] = [];
    await page.route("**/connectors/test", async (route) => {
      sent.push(route.request().postDataJSON());
      await route.fulfill({ json: answer });
    });
    await page.goto("/chat");
    await expect(page.getByText("a PostgreSQL, MySQL, MariaDB or SQL Server")).toBeVisible();
    await page.getByRole("button", { name: "SQL Database" }).click();
    await expect(page.getByText("PostgreSQL (including Supabase and Neon), MySQL, MariaDB or SQL Server")).toBeVisible();
    await page.getByLabel("Connection string").fill(URL);
    await page.getByLabel("Table name (optional)").fill("order_lines");
    await page.getByRole("button", { name: "Test and connect" }).click();
    await expect(page.getByRole("status").or(page.getByText("order_lines")).first()).toBeVisible();
    expect(sent).toEqual([expect.objectContaining({ type: "sql", connection_string: URL,
                                                     table_name: "order_lines" })]);
    // the password is only ever in the input the user typed it into, never in rendered text
    await expect(page.locator("body")).not.toContainText(PASSWORD);
  });
}

// Phase 5.8b: the password has a field of its own that hides it; the form puts it into the
// string URL-encoded (an '@' or ':' in it needs no hand-encoding), and the page never shows it.
test("the password field hides the password and sends it encoded in the string", async ({ page }) => {
  const pw = "p@ss:w0rd/5_8b";
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page);
  const sent: Record<string, unknown>[] = [];
  await page.route("**/connectors/test", async (route) => {
    sent.push(route.request().postDataJSON());
    await route.fulfill({ json: { success: true, message: "Connection successful" } });
  });
  await page.goto("/chat");
  await page.getByRole("button", { name: "SQL Database" }).click();
  await page.getByLabel("Connection string").fill("mssql://reader@db.example.com:1433/acme");
  const field = page.getByLabel("Password", { exact: true });
  await expect(field).toHaveAttribute("type", "password");
  await field.fill(pw);
  await page.getByRole("button", { name: "Test and connect" }).click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent).toEqual([expect.objectContaining({
    connection_string: `mssql://reader:${encodeURIComponent(pw)}@db.example.com:1433/acme` })]);
  await expect(page.locator("body")).not.toContainText(pw);
  await expect(page.locator("body")).not.toContainText(encodeURIComponent(pw));
});
