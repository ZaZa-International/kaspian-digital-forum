#!/bin/sh
# Production entrypoint.
#
# The data volume may have been created root-owned by an older image (the
# deploy host reuses an existing named volume, so the image's chown does not
# reach it). When started as root we fix ownership of the data dir, then drop
# to the unprivileged appuser for the actual server process (standard
# postgres/mysql-style pattern: only this short entrypoint runs as root).
#
# When already started non-root (e.g. the deploy compose pins `user:`), we
# exec directly; store.py then gracefully falls back to /tmp and labels it in
# /api/health. This script can only help, never break startup.
set -e

DATA_DIR="${SCT_DATA_DIR:-/app/backend/app/data}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR" 2>/dev/null || true
  chown -R appuser:appuser "$DATA_DIR" 2>/dev/null || true
  exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
fi

exec "$@"
