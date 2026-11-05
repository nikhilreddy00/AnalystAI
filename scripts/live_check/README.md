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
Spend: each overview is a paid run (~$0.13) and a targeted question runs the whole pipeline too
(~$0.11); announce it first. What the stack has spent, browser runs included:
`uv run python scripts/live_check/spend.py --since "<start>" --budget <estimate>` (exit 1 when over).
Domain end check for the finished domains at $0: `scripts/ui_check` with `make_fixture.py --dataset <name>`.
