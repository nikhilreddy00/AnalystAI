#!/usr/bin/env bash
# Local scratch Postgres + MySQL for the database tests (CI has its own services; no Docker here).
#   eval "$(scripts/test_dbs.sh up)"   # start both (initialised on first use), export the URLs
#   scripts/test_dbs.sh down           # stop both
# Data and the generated MySQL password live in .live/dbs (gitignored); loopback only.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)/.live/dbs"
PG_PORT=54330
MY_PORT=33069
SOCK=/tmp/aai-mysql.sock   # a short path: macOS caps socket paths at 104 bytes

case "${1:-}" in
up)
  mkdir -p "$DIR"
  if [ ! -d "$DIR/pg" ]; then initdb -D "$DIR/pg" -U postgres --auth=trust >/dev/null; fi
  if ! pg_ctl -D "$DIR/pg" status >/dev/null 2>&1; then
    pg_ctl -D "$DIR/pg" -o "-p $PG_PORT -c unix_socket_directories='' -c listen_addresses=localhost" \
      -l "$DIR/pg.log" -w start >/dev/null
  fi
  createdb -h localhost -p "$PG_PORT" -U postgres analystai_eval 2>/dev/null || true
  if [ ! -d "$DIR/mysql" ]; then
    mysqld --initialize-insecure --datadir="$DIR/mysql" --user="$(whoami)" 2>/dev/null
  fi
  if ! mysqladmin --socket="$SOCK" -uroot ping >/dev/null 2>&1; then
    nohup mysqld --datadir="$DIR/mysql" --port="$MY_PORT" --bind-address=127.0.0.1 --socket="$SOCK" \
      --mysqlx=OFF --log-error="$DIR/mysql.err" --pid-file="$DIR/mysql.pid" >/dev/null 2>&1 &
    for _ in $(seq 60); do mysqladmin --socket="$SOCK" -uroot ping >/dev/null 2>&1 && break; sleep 1; done
  fi
  [ -f "$DIR/mysql.pw" ] || openssl rand -hex 12 > "$DIR/mysql.pw"
  PW=$(cat "$DIR/mysql.pw")
  mysql --socket="$SOCK" -uroot -e "CREATE DATABASE IF NOT EXISTS analystai_eval CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
    CREATE USER IF NOT EXISTS 'analyst'@'127.0.0.1' IDENTIFIED BY '$PW';
    ALTER USER 'analyst'@'127.0.0.1' IDENTIFIED BY '$PW';
    GRANT ALL ON *.* TO 'analyst'@'127.0.0.1';"   # tests create a database per worker
  echo "export TEST_PG_URL=postgresql+psycopg://postgres@localhost:$PG_PORT/analystai_eval"
  echo "export TEST_MYSQL_URL=mysql+pymysql://analyst:$PW@127.0.0.1:$MY_PORT/analystai_eval"
  ;;
down)
  mysqladmin --socket="$SOCK" -uroot shutdown 2>/dev/null || true
  pg_ctl -D "$DIR/pg" stop >/dev/null 2>&1 || true
  echo "stopped"
  ;;
*)
  echo "usage: $0 up|down" >&2
  exit 2
  ;;
esac
