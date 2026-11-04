# Live check kit (a domain's end-of-domain check)

The real API + worker in production mode on asyncpg, the real frontend, a mocked Supabase:
1. `scripts/live_check/up.sh` (Postgres in `.pgdata/`, logs in `.live/`).
2. API path: `uv run python scripts/live_check/ask.py <file> "Give me an overview of this dataset."`
   prints tiles, flags and cost; follow-ups: `ask.py - "<question>" --session <id>`.
3. Browser (Playwright MCP at 1440 px): run the line `uv run python scripts/live_check/session_token.py`
   prints with `browser_evaluate` on http://127.0.0.1:3100, then open `/chat`. Wait for a run
   by its result (the report's tiles), never a fixed sleep; uploads: `setInputFiles` on
   `input[type=file]` (the drop zone covers it).
4. Dashboard: `/dashboard`; apply a filter and read a tile AND a chart before/after (ROADMAP 5.7).
5. `scripts/live_check/down.sh` (`--pg` also stops Postgres).
Spend: each overview is a paid run (~$0.13); announce it first.
