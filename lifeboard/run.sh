#!/usr/bin/env bash
# lifeboard launcher. Binds to all interfaces so it's reachable over Tailscale.
set -e
cd "$(dirname "$0")"
PORT="${LIFEBOARD_PORT:-8800}"
exec uvicorn app:app --host 0.0.0.0 --port "$PORT"
