import { expect, type Page } from "@playwright/test";

/** Upload a tiny CSV and wait until the question box takes input. A file set before React has
 *  hydrated /chat (the dev server compiles it on first visit) fires no change event and the box
 *  stays disabled (CI flake, plan v2): set it again until the box is enabled. The same bytes are
 *  the same dataset version, so a second set changes nothing. */
export async function upload(page: Page, name: string) {
  const input = page.getByLabel("Ask a question about your data");
  await expect(async () => {
    await page.locator('input[type="file"]').setInputFiles({
      name, mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n"),
    });
    await expect(input).toBeEnabled({ timeout: 3_000 });
  }).toPass({ timeout: 30_000 });
  return input;
}
