#!/usr/bin/env bash
set -o errexit

PORT="${PORT:-10000}"
HOST="${HOST:-0.0.0.0}"

echo "==> Starting Punjab AI Terminal on ${HOST}:${PORT}..."
exec python run.py --host "${HOST}" --port "${PORT}"
