// Phase 5.6a reconnect test: the real frontend (useAnalysis + SSE parsing) against the mock
// backend in scripts/ui_check (no keys, no API spend). `npx playwright test`.
import { defineConfig } from "@playwright/test";

const fixtures = "../scripts/ui_check/fixtures";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  workers: 1, // the mock serves one run at a time
  use: { baseURL: "http://127.0.0.1:3100", browserName: "chromium" },
  webServer: [
    { command: `python3 ../scripts/ui_check/mock_api.py ${fixtures} 8100`, url: "http://127.0.0.1:8100/health" },
    { command: `python3 ../scripts/ui_check/mock_api.py ${fixtures} 54321`, url: "http://127.0.0.1:54321/health" },
    {
      command: "./node_modules/.bin/next dev -p 3100 -H 127.0.0.1",
      url: "http://127.0.0.1:3100/chat",
      timeout: 180_000,
      reuseExistingServer: true,
      env: {
        NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:54321",
        NEXT_PUBLIC_SUPABASE_ANON_KEY: "x",
        NEXT_PUBLIC_API_URL: "http://127.0.0.1:8100",
      },
    },
  ],
});
