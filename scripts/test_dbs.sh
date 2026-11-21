#!/usr/bin/env bash
# Local scratch Postgres + MySQL (Homebrew) and MariaDB + SQL Server (colima containers; SQL
# Server under Rosetta) for the database tests. CI has its own services.
#   eval "$(scripts/test_dbs.sh up)"   # start all four (initialised on first use), export the URLs
#   scripts/test_dbs.sh down           # stop all four and colima
# Data and the generated passwords live in .live/dbs (gitignored). The containers publish on
# every host address (the live check reaches them by the colima VM's address in production mode).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)/.live/dbs"
PG_PORT=54330
MY_PORT=33069
SOCK=/tmp/aai-mysql.sock   # a short path: macOS caps socket paths at 104 bytes
MARIA_PORT=33070
MSSQL_PORT=14330
MARIA_IMAGE=mariadb:11.4                                              # the same pins as CI
MSSQL_IMAGE=mcr.microsoft.com/mssql/server:2022-CU28-ubuntu-22.04

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
  # 5.8b: MariaDB and SQL Server in colima (SQL Server under Rosetta); tests/conftest.py creates
  # the databases and the SQL Server reader/writer logins from these URLs
  [ -f "$DIR/mssql.pw" ] || echo "Aa1-$(openssl rand -hex 12)" > "$DIR/mssql.pw"
  SA=$(cat "$DIR/mssql.pw")
  colima status >/dev/null 2>&1 || \
    colima start --network-address --vm-type vz --vz-rosetta --memory 6 --cpu 4 >/dev/null 2>&1
  docker start aai-mariadb >/dev/null 2>&1 || docker run -d --name aai-mariadb \
    -e MARIADB_ROOT_PASSWORD="$SA" -p "$MARIA_PORT:3306" "$MARIA_IMAGE" >/dev/null
  docker start aai-mssql >/dev/null 2>&1 || docker run -d --name aai-mssql --platform linux/amd64 \
    -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD="$SA" -p "$MSSQL_PORT:1433" "$MSSQL_IMAGE" >/dev/null
  for _ in $(seq 90); do
    docker exec aai-mariadb mariadb -uroot -p"$SA" -e 'SELECT 1' >/dev/null 2>&1 && \
      docker exec aai-mssql /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P "$SA" \
        -Q 'SELECT 1' >/dev/null 2>&1 && break
    sleep 1
  done
  ODBC="driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
  # macOS: Microsoft's ODBC driver dlopens OpenSSL 3 from /opt/homebrew/opt/openssl, which
  # Homebrew now points at OpenSSL 4 (the driver then says "OpenSSL library could not be loaded")
  [ -d /opt/homebrew/opt/openssl@3/lib ] && echo "export DYLD_LIBRARY_PATH=/opt/homebrew/opt/openssl@3/lib"
  echo "export TEST_MARIADB_URL=mariadb+pymysql://root:$SA@127.0.0.1:$MARIA_PORT/analystai_eval"
  echo "export TEST_MSSQL_URL='mssql+pyodbc://sa:$SA@127.0.0.1:$MSSQL_PORT/analystai_eval?$ODBC'"
  echo "export TEST_MSSQL_READER_URL='mssql://aai_reader:$SA@127.0.0.1:$MSSQL_PORT/analystai_eval?TrustServerCertificate=yes'"
  echo "export TEST_MSSQL_WRITER_URL='mssql://aai_writer:$SA@127.0.0.1:$MSSQL_PORT/analystai_eval?TrustServerCertificate=yes'"
  ;;
down)
  docker stop aai-mariadb aai-mssql >/dev/null 2>&1 || true
  colima stop >/dev/null 2>&1 || true
  mysqladmin --socket="$SOCK" -uroot shutdown 2>/dev/null || true
  pg_ctl -D "$DIR/pg" stop >/dev/null 2>&1 || true
  echo "stopped"
  ;;
*)
  echo "usage: $0 up|down" >&2
  exit 2
  ;;
esac
