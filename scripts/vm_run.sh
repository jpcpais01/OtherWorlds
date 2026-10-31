#!/usr/bin/env bash
# Start the server plus an HTTPS tunnel on a plain GPU machine.
# Prints the https://….trycloudflare.com link to open on the phone.
set -euo pipefail
cd "$(dirname "$0")/.."
source ~/ow-venv/bin/activate
pkill -f "cloudflared tunnel" 2>/dev/null || true
cloudflared tunnel --no-autoupdate --url http://localhost:8080 > /tmp/tunnel.log 2>&1 &
for _ in $(seq 30); do
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /tmp/tunnel.log | head -1 || true)
  [ -n "$URL" ] && break; sleep 1
done
export PUBLIC_URL=${URL:-"(tunnel link not found yet, see /tmp/tunnel.log)"}  # shown when the server is ready
exec bash scripts/run.sh "$@"
