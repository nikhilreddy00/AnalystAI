# UI check kit

Drives the real frontend against a mock backend, so screens can be checked without
Supabase keys or API credits. Auth and the backend are mocked; components, the
`useAnalysis` hook and SSE parsing are real.

1. `uv run python scripts/ui_check/make_fixture.py scripts/ui_check/fixtures` (writes the dashboard JSON)
2. Start the mocks: `mock_api.py fixtures 8100` and `mock_api.py fixtures 54321`
3. `cd frontend && NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321 NEXT_PUBLIC_SUPABASE_ANON_KEY=x
   NEXT_PUBLIC_API_URL=http://127.0.0.1:8100 ./node_modules/.bin/next dev -p 3100 -H 127.0.0.1`
4. In Playwright, set a fake session at localStorage `sb-127-auth-token` (any non-expired
   `expires_at`), upload any file, and ask a question. Pick the run with `fixtures/scenario`.
