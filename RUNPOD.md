# RunPod setup, step by step

Steps marked **💰** cost money. Everything else is free.

What you'll pay, roughly: an H100 pod (for the default SDXL engine) is about
**$2–3 per hour**; an L40 or RTX 4090 with the lighter `streamdiffusion`
engine is about **$0.35–0.90 per hour**
while it exists and is running (RunPod shows the exact price before you
deploy). Its disks cost a few cents per GB per month, **and keep costing even
when the pod is stopped**, until you terminate it. A one-hour H100 test session is
about $3.

---

## 1. Create an account (free)

Sign up at <https://www.runpod.io> and log in to the console.

## 2. 💰 Add credit

RunPod is prepaid. Go to **Billing** and add the minimum amount (about $10).
This is the only money that can be spent; leave **auto-pay off** so it can't
top itself up.

## 3. Pick a GPU (free until you click Deploy)

Go to **Pods → Deploy**.

- Choose **Secure Cloud** (it gives the pod a public IP, which the optional
  WebRTC relay in step 8 needs. Community Cloud is cheaper and fine if you
  only use WebSocket mode).
- Choose **H100** (NVL, SXM or PCIe). For the lighter engine, an L40, RTX
  4090 or 3090 is enough. Avoid RTX 5090 / PRO 6000 / B200: they need a newer
  PyTorch than this setup pins. Prefer a datacenter near you: the network
  round trip, not the GPU, is usually what limits FPS.

## 4. Configure the pod (free)

- **Template:** `RunPod Pytorch 2.4.0`
  (`runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`).
- Click **Edit Template**:
  - Container Disk: **50 GB** (the SDXL weights are ~8 GB)
  - No volume needed
  - Expose HTTP Ports: **8080** (keep 8888 if listed)
  - Expose TCP Ports: **22, 3478** (3478 carries WebRTC through a TURN relay)
- Do **not** attach a Network Volume (those bill separately and outlive the pod).
- Pricing: **On-Demand**. (Spot is cheaper but can be shut off mid-session.)

## 5. 💰 Deploy

Click **Deploy On-Demand**. **Billing starts now** and runs every second the
pod exists and is running. Wait until the pod shows *Running* (1–3 minutes).

## 6. Install and start the server

In the pod, click **Connect → Start Web Terminal → Connect to Web Terminal**
(or open Jupyter Lab → Terminal), then:

```bash
cd /workspace
git clone -b claude/project-thread-izhm9c https://github.com/jpcpais01/OtherWorlds
cd OtherWorlds
bash scripts/setup.sh      # ~3 min, ends with "setup OK on NVIDIA H100 ..."
bash scripts/run.sh        # first run downloads ~8 GB of weights
                           # (lighter engine: bash scripts/run.sh --backend streamdiffusion)
```

Wait for `engine ready: sdxl-turbo` and `Running on http://0.0.0.0:8080`.
Leave this terminal open.

## 7. Open it on your phone

In the RunPod console: **Connect → HTTP Service [Port 8080]**. It opens
`https://<pod-id>-8080.proxy.runpod.net`. Open that URL on your phone
(Chrome on Android or Safari on iOS), tap **Start**, allow the camera.

With **Link: auto** it tries WebRTC for 10 seconds, then falls back to
WebSocket, which always works through RunPod's proxy. The HUD's first line
says which one is in use.

## 8. WebRTC (automatic if TCP 3478 is exposed)

RunPod pods don't accept inbound UDP, so WebRTC goes through a TURN relay
that `run.sh` starts on the pod. Check that it printed a
`turn:<ip>:<port>?transport=tcp` line under `phone ICE servers`. If not, the
pod has no public IP or port 3478 isn't exposed, and WebSocket is used.
Compare the two with the **Link** selector; the HUD shows the latency of each.

## 9. Stop billing when you're done

- **Stop** (⏹) only pauses the GPU charge. The disks **keep billing** while
  stopped. Fine for a break of a day or two, not for "I'm done".
- **To stop all billing: Terminate.** Pods → your pod → **⏹ Stop**, then the
  **🗑 Terminate** button. This deletes the pod and both of its disks
  (the model weights download again next time, about a minute).
- Check **Storage → Network Volumes** is empty (delete any you see).
- Check the top bar / **Billing** shows spend rate **$0.000/hr**.

Leftover credit stays in your account for next time.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Phone page shows 502 / "not ready" | The server isn't running yet, or crashed: check the terminal. |
| `401` while downloading the model | Create a token at huggingface.co/settings/tokens, then `export HF_TOKEN=...` and rerun `run.sh`. |
| `StreamDiffusion unavailable … falling back to diffusers` | Works, just slower. Paste the error in the thread. |
| CUDA out of memory | Pick a lower resolution. |
| Camera doesn't start on phone | Must use the `https://…proxy.runpod.net` URL; allow camera permission. |
| WebRTC never connects | Use WebSocket, or check step 8 printed a `turn:` line. |
