#!/usr/bin/env bash
# Start the server on port 8080. Extra args go to server/app.py (e.g. --backend fake).
set -euo pipefail
cd "$(dirname "$0")/.."
export HF_HOME=${HF_HOME:-/workspace/hf}  # model weights cache
export COMPILE=${COMPILE:-1}  # torch.compile the SDXL UNet: ~2.7x faster, 1-3 min startup; COMPILE=0 to skip
echo
echo "OtherWorlds server"
echo "------------------"
# If TCP port 3478 is exposed, start a TURN relay so WebRTC can work too.
if [ -n "${RUNPOD_TCP_PORT_3478:-}" ]; then
  if relay=$(bash scripts/turn.sh 2>&1); then echo "▸ WebRTC relay: ${relay##*$'\n'}"
  else echo "▸ WebRTC relay: failed, WebSocket mode still works"; fi
fi
[ -f /workspace/.turn_pass ] && export TURN_PASS=$(cat /workspace/.turn_pass)
exec python server/app.py --port 8080 "$@"
