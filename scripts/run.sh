#!/usr/bin/env bash
# Start the server on port 8080. Extra args go to server/app.py (e.g. --backend fake).
set -euo pipefail
cd "$(dirname "$0")/.."
export HF_HOME=/workspace/hf  # keep model weights on the pod volume
[ -f /workspace/.turn_pass ] && export TURN_PASS=$(cat /workspace/.turn_pass)
exec python server/app.py --port 8080 "$@"
