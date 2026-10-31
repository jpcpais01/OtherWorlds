# OtherWorlds

Prototype of real-time AI video restyling for a future AR headset. A phone
browser streams its camera to a rented GPU, which restyles each frame with
**SDXL-Turbo guided by a Canny-edge ControlNet** (one denoising step) and
streams it back. A lighter StreamDiffusion + sd-turbo engine is kept for
cheaper GPUs (`--backend streamdiffusion`).

```
phone camera ─▶ canvas (square crop + frame-ID code) ─▶ WebRTC ─▶ aiortc ─▶ SDXL-Turbo + ControlNet (1 step, tiny VAE) ─▶ WebRTC ─▶ phone screen
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

1. **WebRTC** (aiortc). RunPod pods take no inbound UDP, so it goes through
   a TURN relay: `scripts/run.sh` starts one on the pod automatically when TCP
   port 3478 is exposed (or set `TURN_URL`/`TURN_USER`/`TURN_PASS` for a
   hosted one).
2. **WebSocket + JPEG.** Works through RunPod's HTTPS proxy with no setup. In
   `auto` mode the phone tries WebRTC for 10 s and falls back to this. The
   phone keeps just enough frames in flight to cover the round trip, so a
   long RTT doesn't cap FPS.

The WebRTC path restyles only the newest frame and drops the rest, so a slow
GPU lowers FPS rather than adding lag.

## Layout

```
server/app.py        aiohttp: static client, /config, /offer (WebRTC), /ws (fallback)
server/restyler.py   engines: sdxl (default), streamdiffusion, diffusers, fake (CPU, for testing)
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

- `Strength` picks how much noise goes on the camera frame (SDXL-Turbo's
  timesteps 249/499/749/999). Higher = stronger style; the ControlNet keeps
  the scene's outlines, so even `max` stays recognisable.
- `CN_SCALE` (env, default 0.6) sets how strictly the output follows the
  camera's edges.
- Style, strength and resolution changes apply on the next frame.
- Next steps for speed: `torch.compile` / TensorRT on the UNet and ControlNet.
  For temporal stability: StreamDiffusionV2.
