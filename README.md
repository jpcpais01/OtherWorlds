# OtherWorlds

Prototype of real-time AI video restyling for a future AR headset. A phone
browser streams its camera to a rented GPU, which restyles each frame with
**StreamDiffusion + sd-turbo** and streams it back.

```
phone camera ─▶ canvas (square crop + frame-ID code) ─▶ WebRTC ─▶ aiortc ─▶ StreamDiffusion (1 step, tiny VAE) ─▶ WebRTC ─▶ phone screen
                                                        └── fallback: WebSocket + JPEG ──┘
```

- **Styles:** Ghibli, cyberpunk, zombie apocalypse, or any custom prompt.
- **Controls:** resolution (256–768 px), style strength, transport, front/back camera.
- **HUD:** shown / sent / GPU FPS, true end-to-end latency, RTT, and per-stage
  server timings (queue, decode, pre, infer, post, encode) plus the leftover
  network + codec + buffering time.

Setup on RunPod, step by step: **[RUNPOD.md](RUNPOD.md)**.

## How the latency number is measured

Over WebRTC you can't tag a video frame with an ID, so the phone paints a
12-bit black/white code along the bottom edge of each frame it sends. The
server reads it, restyles the frame, and paints the same code back on. The
phone reads the code off each displayed frame and subtracts the time it
captured that frame: that is real camera-to-screen latency (minus the camera
sensor's own delay). The bottom 1/32 of the picture is clipped so you don't
see the code. Over WebSocket the ID simply travels in the message header.

## Transports

1. **WebRTC** (aiortc). Lowest latency when UDP works. RunPod pods take no
   inbound UDP, so it needs a TURN relay: `scripts/turn.sh` runs one on the pod
   over an exposed TCP port, or set `TURN_URL`/`TURN_USER`/`TURN_PASS` for a
   hosted one.
2. **WebSocket + JPEG.** Works through RunPod's HTTPS proxy with no setup. In
   `auto` mode the phone tries WebRTC for 10 s and falls back to this.

Both sides drop stale frames instead of queueing them, so a slow GPU lowers
FPS rather than adding lag.

## Layout

```
server/app.py        aiohttp: static client, /config, /offer (WebRTC), /ws (fallback)
server/restyler.py   engines: streamdiffusion (default), diffusers (fallback), fake (CPU, for testing)
server/stamp.py      frame-ID code
client/              index.html + app.js, no build step
scripts/             setup.sh, run.sh, turn.sh for the pod
```

## Run locally without a GPU

The `fake` engine applies a color tint on the CPU, which is enough to test
networking and the HUD (use `localhost` in a desktop browser; phones need HTTPS).

```
pip install aiohttp aiortc numpy pillow
python server/app.py --backend fake
```

## Tuning

- `Strength` maps to StreamDiffusion's `t_index_list` (one denoising step).
  Higher = stronger style, less like the real scene, more flicker.
- Resolution and strength changes rebuild the pipeline (~1 s pause); style
  changes are instant.
- Next steps for speed: StreamDiffusion's TensorRT acceleration (2–3x), and
  for temporal stability StreamDiffusionV2 or a ControlNet-guided pipeline.
