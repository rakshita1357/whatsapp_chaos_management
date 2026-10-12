#!/usr/bin/env bash
# Start a throwaway local Postgres 16 without Docker (used where the docker daemon is unavailable).
# Usage: scripts/localpg.sh start|stop   -> listens on 127.0.0.1:5432, user/db/password wcm
set -euo pipefail
BIN=${PG_BIN:-/usr/lib/postgresql/16/bin}
DIR=${PGDATA_DIR:-/tmp/wcm-pg}
run() { if [ "$(id -u)" = 0 ]; then su postgres -s /bin/bash -c "$*"; else bash -c "$*"; fi; }
case "${1:-start}" in
  start)
    if [ ! -d "$DIR" ]; then
      mkdir -p "$DIR"; [ "$(id -u)" = 0 ] && chown postgres "$DIR"
      run "$BIN/initdb -D $DIR -U wcm --auth=trust >/dev/null"
    fi
    run "$BIN/pg_ctl -D $DIR -l $DIR/log -o '-c listen_addresses=127.0.0.1' -w start" || true
    run "$BIN/psql -h 127.0.0.1 -U wcm -d postgres -tc \"select 1 from pg_database where datname='wcm'\"" | grep -q 1 \
      || run "$BIN/createdb -h 127.0.0.1 -U wcm wcm"
    ;;
  stop) run "$BIN/pg_ctl -D $DIR -m fast stop" ;;
esac
