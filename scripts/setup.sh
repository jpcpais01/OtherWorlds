#!/usr/bin/env bash
# One-time install on the RunPod pod (PyTorch 2.4 template). ~3 minutes.
set -euo pipefail
cd "$(dirname "$0")/.."
pip install -r server/requirements.txt
# --no-deps: StreamDiffusion's own pins (old onnx/protobuf) break modern installs,
# and it only needs what requirements.txt already provides.
pip install --no-deps "git+https://github.com/cumulo-autumn/StreamDiffusion.git@main"
python -c "import torch, streamdiffusion, aiortc; assert torch.cuda.is_available(), 'no GPU visible'; print('setup OK on', torch.cuda.get_device_name())"
