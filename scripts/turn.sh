#!/usr/bin/env bash
# Optional: run a TURN relay (coturn) on the pod so WebRTC can work even
# though RunPod pods take no inbound UDP. The phone reaches it over TCP via
# the pod's exposed TCP port 3478; the server reaches it locally.
# Needs "3478" in the pod's "Expose TCP Ports" and a public IP.
set -euo pipefail
if [ -z "${RUNPOD_TCP_PORT_3478:-}" ]; then
  echo "TCP port 3478 isn't exposed on this pod; WebSocket mode will be used instead."; exit 1
fi
command -v turnserver >/dev/null || { apt-get update -qq && apt-get install -y -qq coturn; }
[ -f /workspace/.turn_pass ] || head -c 12 /dev/urandom | base64 | tr -d '/+=' > /workspace/.turn_pass
IP=$(hostname -I | awk '{print $1}')
cat > /workspace/turnserver.conf <<CONF
listening-port=3478
relay-ip=$IP
min-port=49160
max-port=49200
realm=otherworlds
lt-cred-mech
user=otherworlds:$(cat /workspace/.turn_pass)
fingerprint
no-tls
no-dtls
no-cli
log-file=/workspace/turn.log
CONF
pkill turnserver 2>/dev/null || true
turnserver -c /workspace/turnserver.conf -o
echo "TURN running: phone will use turn:${RUNPOD_PUBLIC_IP}:${RUNPOD_TCP_PORT_3478}?transport=tcp"

