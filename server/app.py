"""OtherWorlds prototype server: phone camera in, restyled video out.

Transport 1 (preferred): WebRTC via aiortc. Video both ways, a data channel
for controls and per-frame timings.
Transport 2 (fallback): a WebSocket carrying JPEG frames, for when WebRTC
can't connect (RunPod pods have no inbound UDP).

Run:  python server/app.py --backend auto --port 8080
"""
import argparse
import asyncio
import io
import json
import os
import struct
import time
from fractions import Fraction
from pathlib import Path

import numpy as np
from aiohttp import WSMsgType, web
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.codecs import h264, vpx
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack
from av import VideoFrame
from PIL import Image

import stamp
from restyler import STYLES, Restyler

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"

# aiortc's default video bitrate caps (1.5 Mbps VP8) look blurry at 512px.
vpx.DEFAULT_BITRATE, vpx.MAX_BITRATE = 2_000_000, 4_000_000
h264.DEFAULT_BITRATE, h264.MAX_BITRATE = 2_000_000, 4_000_000

restyler: Restyler = None
pcs = set()


def ice_servers():
    """ICE servers for (phone, server).

    TURN_URL/TURN_USER/TURN_PASS: an external TURN service; both sides use it.
    Otherwise, if scripts/turn.sh runs coturn on the pod and TCP port 3478 is
    exposed, RunPod sets RUNPOD_PUBLIC_IP and RUNPOD_TCP_PORT_3478 and only the
    phone needs it (the server reaches coturn's relay ports locally).
    """
    phone = [{"urls": "stun:stun.l.google.com:19302"}]
    server = []
    user, pw = os.environ.get("TURN_USER", "otherworlds"), os.environ.get("TURN_PASS")
    if os.environ.get("TURN_URL"):
        turn = {"urls": os.environ["TURN_URL"].split(","), "username": user, "credential": pw}
        phone.append(turn)
        server.append(turn)
    elif pw and os.environ.get("RUNPOD_TCP_PORT_3478"):
        ip, port = os.environ["RUNPOD_PUBLIC_IP"], os.environ["RUNPOD_TCP_PORT_3478"]
        phone.append({"urls": f"turn:{ip}:{port}?transport=tcp", "username": user, "credential": pw})
    return phone, server


class Stats:
    """Rolling server-side FPS."""

    def __init__(self):
        self.n, self.t0, self.fps, self.dropped = 0, time.perf_counter(), 0.0, 0

    def tick(self):
        self.n += 1
        dt = time.perf_counter() - self.t0
        if dt >= 1:
            self.fps, self.n, self.t0 = self.n / dt, 0, time.perf_counter()


class RestyledTrack(MediaStreamTrack):
    """Outgoing video track. Always restyles the newest incoming frame and
    drops the rest, so a slow GPU lowers FPS instead of building up lag."""

    kind = "video"

    def __init__(self, source, send_meta):
        super().__init__()
        self.source, self.send_meta = source, send_meta
        self.latest, self.fresh = None, asyncio.Event()
        self.stats, self.start = Stats(), time.time()
        self.reader = asyncio.ensure_future(self._read())

    async def _read(self):
        try:
            while True:
                frame = await self.source.recv()
                if self.latest is not None:
                    self.stats.dropped += 1
                self.latest = (frame, time.perf_counter())
                self.fresh.set()
        except MediaStreamError:
            self.stop()

    async def recv(self):
        await self.fresh.wait()
        self.fresh.clear()
        frame, arrived = self.latest
        self.latest = None
        t0 = time.perf_counter()
        img = frame.to_ndarray(format="rgb24")
        frame_id = stamp.read(img)
        t1 = time.perf_counter()
        out, t = await restyler.process(asyncio.get_running_loop(), img)
        t2 = time.perf_counter()
        stamp.write(out, frame_id)
        vf = VideoFrame.from_ndarray(out, format="rgb24")
        vf.pts, vf.time_base = int((time.time() - self.start) * 90000), Fraction(1, 90000)
        t3 = time.perf_counter()
        self.stats.tick()
        self.send_meta({
            "type": "frame", "id": frame_id, "fps": round(self.stats.fps, 1), "dropped": self.stats.dropped,
            "srv": {"queue": (t0 - arrived) * 1e3, "decode": (t1 - t0) * 1e3, **t,
                    "gpu_wait": (t2 - t1) * 1e3 - sum(t.values()), "encode": (t3 - t2) * 1e3,
                    "total": (t3 - arrived) * 1e3},
        })
        return vf

    def stop(self):
        self.reader.cancel()
        super().stop()


async def handle_control(msg, reply):
    """Shared control protocol for both transports."""
    loop = asyncio.get_running_loop()
    if msg.get("type") == "ping":
        reply({"type": "pong", "t": msg["t"]})
    elif msg.get("type") == "config":
        reply({"type": "state", **await restyler.configure(loop, msg)})


async def offer(request):
    params = await request.json()
    _, server_ice = ice_servers()
    pc = RTCPeerConnection(RTCConfiguration([RTCIceServer(**s) for s in server_ice]))
    pcs.add(pc)
    channel = {}

    def send_meta(m):
        ch = channel.get("ctrl")
        if ch and ch.readyState == "open":
            ch.send(json.dumps(m))

    @pc.on("datachannel")
    def on_datachannel(ch):
        channel["ctrl"] = ch

        @ch.on("message")
        def on_message(m):
            asyncio.ensure_future(handle_control(json.loads(m), send_meta))

    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            pc.addTrack(RestyledTrack(track, send_meta))

    @pc.on("connectionstatechange")
    async def on_state():
        print("webrtc:", pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
    await pc.setLocalDescription(await pc.createAnswer())
    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})


async def websocket(request):
    """Fallback transport. Phone -> server: uint32 id + JPEG.
    Server -> phone: uint32 id + uint32 meta length + meta JSON + JPEG."""
    ws = web.WebSocketResponse(max_msg_size=8 * 1024 * 1024)
    await ws.prepare(request)
    loop = asyncio.get_running_loop()
    # FIFO: the phone caps how many frames it has in flight, so this stays short
    # and every frame gets an answer (the phone counts replies to pace itself).
    queue, stats = asyncio.Queue(), Stats()

    def reply(m):
        if not ws.closed:
            asyncio.ensure_future(ws.send_str(json.dumps(m)))

    async def worker():
        while not ws.closed:
            frame_id, jpeg, arrived = await queue.get()
            t0 = time.perf_counter()
            img = np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))
            t1 = time.perf_counter()
            out, t = await restyler.process(loop, img)
            t2 = time.perf_counter()
            buf = io.BytesIO()
            Image.fromarray(out).save(buf, format="JPEG", quality=80)
            t3 = time.perf_counter()
            stats.tick()
            meta = json.dumps({
                "type": "frame", "id": frame_id, "fps": round(stats.fps, 1), "dropped": stats.dropped,
                "srv": {"queue": (t0 - arrived) * 1e3, "decode": (t1 - t0) * 1e3, **t,
                        "gpu_wait": (t2 - t1) * 1e3 - sum(t.values()), "encode": (t3 - t2) * 1e3,
                        "total": (t3 - arrived) * 1e3},
            }).encode()
            if not ws.closed:
                await ws.send_bytes(struct.pack("<II", frame_id, len(meta)) + meta + buf.getvalue())

    task = asyncio.ensure_future(worker())
    try:
        async for m in ws:
            if m.type == WSMsgType.BINARY:
                queue.put_nowait((struct.unpack_from("<I", m.data)[0], m.data[4:], time.perf_counter()))
            elif m.type == WSMsgType.TEXT:
                await handle_control(json.loads(m.data), reply)
    finally:
        task.cancel()
    return ws


async def config(request):
    phone_ice, _ = ice_servers()
    return web.json_response({"iceServers": phone_ice, "styles": list(STYLES), **restyler.state,
                              "engine": restyler.engine.name})


async def index(request):
    return web.FileResponse(CLIENT_DIR / "index.html")


async def on_shutdown(app):
    await asyncio.gather(*(pc.close() for pc in pcs))


def main():
    global restyler
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="auto", choices=["auto", "sdxl", "streamdiffusion", "diffusers", "fake"],
                    help="auto = sdxl (SDXL-Turbo + ControlNet); streamdiffusion = lighter sd-turbo")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    print(f"loading {args.backend} engine (first run downloads the model weights, ~3-8 GB)...")
    restyler = Restyler(args.backend)
    print("phone ICE servers:", [s["urls"] for s in ice_servers()[0]])

    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/config", config)
    app.router.add_post("/offer", offer)
    app.router.add_get("/ws", websocket)
    app.router.add_static("/", CLIENT_DIR)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
