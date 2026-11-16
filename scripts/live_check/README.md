# Live check kit (a domain's end-of-domain check)

The real API + worker in production mode on asyncpg, the real frontend, a mocked Supabase:
1. `scripts/live_check/up.sh` (Postgres in `.pgdata/`, logs in `.live/`).
2. API path: `uv run python scripts/live_check/ask.py <file> "Give me an overview of this dataset."`
   prints tiles, flags and cost; follow-ups: `ask.py - "<question>" --session <id>`.
3. Browser at 1440 px with agent-browser (`npm i -g agent-browser@0.43.0 && agent-browser install`;
   chosen 2026-11-16 over the Playwright MCP: a page snapshot is ~37k characters, a full-page
   screenshot ~190k). The committed regression specs stay Playwright (`frontend/e2e/`, run by CI).
   ```bash
   S=$(agent-browser session id --scope worktree --prefix live)   # your own browser, not a shared one
   ab() { agent-browser --session "$S" "$@"; }   # zsh: a function; `A="agent-browser …"; $A` won't split
   ab set viewport 1440 900; ab open http://127.0.0.1:3100/chat
   ab eval "($(uv run python scripts/live_check/session_token.py))()"   # the login (mock stack: the
                                                 # session object in frontend/e2e/dashboard.spec.ts)
   ab open http://127.0.0.1:3100/chat; ab snapshot -i   # refs @eN; re-snapshot after any re-render
   ab upload 'input[type=file]' <file>                  # the drop zone covers the input
   ab wait --text "<a tile label>"                      # wait on a result, never a fixed sleep
   ab eval '<read numbers, or whole chart paths, from the DOM>'
   ab screenshot 'figure' .live/<name>.png              # one element, never the full page
   ab close                                             # drops the login: set it again after
   ```
4. Dashboard: `/dashboard`; apply a filter and read a tile AND a chart before/after (ROADMAP 5.7).
5. `scripts/live_check/down.sh` (`--pg` also stops Postgres).
Spend: each overview is a paid run (~$0.13) and a targeted question runs the whole pipeline too
(~$0.11); announce it first. What the stack has spent, browser runs included:
`uv run python scripts/live_check/spend.py --since "<start>" --budget <estimate>` (exit 1 when over).
Domain end check for the finished domains at $0: `scripts/ui_check` with `make_fixture.py --dataset <name>`.
