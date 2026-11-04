#!/usr/bin/env bash
# The live stack for a domain's end-of-domain check (production mode, asyncpg, separate worker):
# scratch Postgres on :54329 (data in .pgdata/, never /tmp: temp cleanup deleted it once),
# API :8000, worker, Supabase auth mock :54321, frontend :3100. Logs in .live/. Stop: down.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
mkdir -p .live
if ! pg_isready -q -h localhost -p 54329; then
  [ -d .pgdata ] || initdb -D .pgdata -U postgres -A trust > .live/initdb.log
  pg_ctl -D .pgdata -o "-p 54329 -c unix_socket_directories='' -c listen_addresses=localhost" \
    -l .live/pg.log start > /dev/null
  until pg_isready -q -h localhost -p 54329; do sleep 0.5; done
fi
for db in analystai_eval analystai_live; do
  psql -h localhost -p 54329 -U postgres -tAc "select 1 from pg_database where datname='$db'" \
    | grep -q 1 || createdb -h localhost -p 54329 -U postgres "$db"
done
source scripts/live_check/env.sh
nohup uv run python -m worker < /dev/null > .live/worker.log 2>&1 &
echo $! > .live/pids
nohup uv run uvicorn api.main:app --host 127.0.0.1 --port 8000 < /dev/null > .live/api.log 2>&1 &
echo $! >> .live/pids
nohup uv run python scripts/ui_check/mock_api.py scripts/ui_check/fixtures 54321 < /dev/null > .live/auth.log 2>&1 &
echo $! >> .live/pids
(cd frontend && NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321 NEXT_PUBLIC_SUPABASE_ANON_KEY=x \
  NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 exec ./node_modules/.bin/next dev -p 3100 -H 127.0.0.1) \
  < /dev/null > .live/frontend.log 2>&1 &
echo $! >> .live/pids
until curl -sf http://127.0.0.1:8000/health > /dev/null && curl -sf http://127.0.0.1:3100/ > /dev/null; do sleep 1; done
curl -s http://127.0.0.1:8000/health; echo
echo "up: API :8000, frontend :3100; session: uv run python scripts/live_check/session_token.py"
