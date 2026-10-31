#!/usr/bin/env bash
# One-time install on a plain Ubuntu GPU machine (e.g. a DigitalOcean GPU
# Droplet with the "AI/ML Ready" image, which has the NVIDIA driver). ~5 min.
set -euo pipefail
cd "$(dirname "$0")/.."
nvidia-smi -L || { echo "No NVIDIA driver: pick an image with GPU drivers."; exit 1; }
apt-get update -qq && apt-get install -y -qq python3-venv git curl
python3 -m venv ~/ow-venv
source ~/ow-venv/bin/activate
pip install -q --upgrade pip
pip install -q torch==2.4.0
bash scripts/setup.sh
# Free HTTPS tunnel, so the phone browser allows the camera (no account needed).
curl -sL -o /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x /usr/local/bin/cloudflared
# WebRTC talks to the machine directly over UDP; open it if a firewall is on.
if command -v ufw >/dev/null && ufw status | grep -q active; then ufw allow 1024:65535/udp; fi
echo "VM setup done. Start with: bash scripts/vm_run.sh"
