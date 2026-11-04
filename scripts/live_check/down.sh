#!/usr/bin/env bash
# Stop the live stack (up.sh) by its pids, never by pattern (pkill -f also matches the caller's
# own shell). Postgres keeps its data in .pgdata/ (pass --pg to stop it too).
cd "$(git rev-parse --show-toplevel)"
if [ -f .live/pids ]; then
  while read -r pid; do pkill -TERM -P "$pid" 2>/dev/null; kill "$pid" 2>/dev/null; done < .live/pids
  rm .live/pids
fi
if [ "${1:-}" = "--pg" ]; then pg_ctl -D .pgdata stop; fi
echo down
