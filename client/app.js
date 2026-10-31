// OtherWorlds phone client: camera -> GPU server -> restyled video, with a HUD.
const $ = (id) => document.getElementById(id);
const cam = $("cam"), out = $("out"), outc = $("outc"), hud = $("hud");

// ---- frame-ID code (must match server/stamp.py) ----
const BITS = 12;
const blockSize = (w) => Math.max(4, Math.floor(w / 32));
function writeStamp(ctx, w, h, id) {
  const b = blockSize(w);
  for (let i = 0; i < BITS; i++) {
    ctx.fillStyle = (id >> i) & 1 ? "#fff" : "#000";
    ctx.fillRect(i * b, h - b, b, b);
  }
}
const stampCanvas = document.createElement("canvas");
const stampCtx = stampCanvas.getContext("2d", { willReadFrequently: true });
function readStamp(video) {
  const w = video.videoWidth, h = video.videoHeight, b = blockSize(w);
  if (!w) return -1;
  stampCanvas.width = BITS * b; stampCanvas.height = b;
  stampCtx.drawImage(video, 0, h - b, BITS * b, b, 0, 0, BITS * b, b);
  const px = stampCtx.getImageData(0, 0, BITS * b, b).data;
  let id = 0;
  for (let i = 0; i < BITS; i++) {
    const o = ((b >> 1) * BITS * b + i * b + (b >> 1)) * 4;
    if ((px[o] + px[o + 1] + px[o + 2]) / 3 > 128) id |= 1 << i;
  }
  return id;
}

// ---- state ----
const S = {
  running: false, mode: null, facing: "environment", res: 512, cfg: null,
  frameId: 0, sentAt: new Map(), inflight: 0,
  pc: null, dc: null, ws: null, server: {},
  // rolling windows (last ~30 samples) for HUD averages
  e2e: [], srv: {}, enc: [], dec: [], rtt: 0, shown: 0, totalShown: 0, sent: 0, fps: { shown: 0, sent: 0 }, note: "",
};
const capture = document.createElement("canvas");
const capCtx = capture.getContext("2d");
const push = (arr, v) => { arr.push(v); if (arr.length > 30) arr.shift(); };
const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);

function nextId() { S.frameId = (S.frameId + 1) % (1 << BITS); return S.frameId; }

// Draw a center-square crop of the camera into the capture canvas.
function drawCamera(stamp) {
  const vw = cam.videoWidth, vh = cam.videoHeight, s = Math.min(vw, vh);
  if (!s) return -1;
  capCtx.drawImage(cam, (vw - s) / 2, (vh - s) / 2, s, s, 0, 0, S.res, S.res);
  const id = nextId();
  if (stamp) writeStamp(capCtx, S.res, S.res, id);
  S.sentAt.set(id, performance.now());
  S.sent++;
  return id;
}

// Run fn once per camera frame. Re-armed when the camera is swapped.
let camGen = 0;
function onCameraFrame(fn) { S.camFn = fn; armCamera(); }
function armCamera() {
  if (!S.camFn) return;
  const gen = ++camGen, rvfc = "requestVideoFrameCallback" in cam;
  const loop = () => {
    if (!S.running || gen !== camGen) return;
    S.camFn();
    rvfc ? cam.requestVideoFrameCallback(loop) : requestAnimationFrame(loop);
  };
  rvfc ? cam.requestVideoFrameCallback(loop) : requestAnimationFrame(loop);
}

function onServerMessage(m) {
  if (m.type === "pong") S.rtt = performance.now() - m.t;
  else if (m.type === "state") S.server = m;
  else if (m.type === "frame") {
    S.server.fps = m.fps; S.server.dropped = m.dropped;
    for (const [k, v] of Object.entries(m.srv)) push((S.srv[k] ||= []), v);
  }
}
function sendControl(m) {
  const s = JSON.stringify(m);
  if (S.dc?.readyState === "open") S.dc.send(s);
  else if (S.ws?.readyState === 1) S.ws.send(s);
}
function sendConfig(extra = {}) { sendControl({ type: "config", res: S.res, strength: +$("strength").value, ...extra }); }

function recordShown(id) {
  const t = S.sentAt.get(id);
  if (t !== undefined) { push(S.e2e, performance.now() - t); S.sentAt.delete(id); }
  S.shown++; S.totalShown++;
}

// ---- transport 1: WebRTC ----
async function startWebRTC() {
  // With a TURN relay configured, only relayed paths can work on RunPod (no
  // inbound UDP), so skip the doomed direct candidates and connect faster.
  const hasTurn = S.cfg.iceServers.some((s) => String(s.urls).startsWith("turn"));
  const pc = (S.pc = new RTCPeerConnection({ iceServers: S.cfg.iceServers, iceTransportPolicy: hasTurn ? "relay" : "all" }));
  S.dc = pc.createDataChannel("ctrl");
  S.dc.onmessage = (e) => onServerMessage(JSON.parse(e.data));
  S.dc.onopen = () => sendConfig({ style: currentStyle() });
  // One sendrecv video transceiver: camera up, restyled video back down.
  pc.addTrack(capture.captureStream(30).getVideoTracks()[0]);
  pc.ontrack = (e) => {
    // Ask the browser not to buffer frames for smoothness: we want them now.
    try { e.receiver.jitterBufferTarget = 0; e.receiver.playoutDelayHint = 0; } catch {}
    out.srcObject = new MediaStream([e.track]); out.play().catch(() => {});
  };
  // Send only a little faster than the GPU restyles. Extra frames are dropped
  // on the server anyway, and on a TCP relay they queue up and add latency.
  let lastDraw = 0;
  onCameraFrame(() => {
    const service = avg(S.srv.total || []) - avg(S.srv.queue || []);  // GPU ms per frame
    const target = service > 0 ? Math.min(30, Math.max(5, 1200 / service)) : 30;
    const now = performance.now();
    if (now - lastDraw < 1000 / target - 4) return;
    lastDraw = now;
    drawCamera(true);
  });

  let last = -1;
  const onOut = () => {
    if (S.mode !== "webrtc") return;
    const id = readStamp(out);
    if (id !== last) { recordShown(id); last = id; }
    "requestVideoFrameCallback" in out ? out.requestVideoFrameCallback(onOut) : requestAnimationFrame(onOut);
  };
  "requestVideoFrameCallback" in out ? out.requestVideoFrameCallback(onOut) : requestAnimationFrame(onOut);

  await pc.setLocalDescription(await pc.createOffer());
  // Non-trickle ICE: wait for candidates (incl. TURN) before sending the offer.
  await new Promise((r) => {
    if (pc.iceGatheringState === "complete") return r();
    pc.onicegatheringstatechange = () => pc.iceGatheringState === "complete" && r();
    setTimeout(r, 4000);
  });
  const res = await fetch("offer", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type }) });
  await pc.setRemoteDescription(await res.json());
}

// ---- transport 2: WebSocket + JPEG ----
async function startWebSocket() {
  out.classList.add("hidden"); outc.classList.remove("hidden");
  const octx = outc.getContext("2d");
  const ws = (S.ws = new WebSocket(new URL("ws", location.href).href.replace(/^http/, "ws")));
  ws.binaryType = "arraybuffer";
  await new Promise((ok, fail) => { ws.onopen = ok; ws.onerror = () => fail(new Error("WebSocket failed")); });
  ws.onmessage = async (e) => {
    if (typeof e.data === "string") return onServerMessage(JSON.parse(e.data));
    S.inflight = Math.max(0, S.inflight - 1);
    const dv = new DataView(e.data), id = dv.getUint32(0, true), n = dv.getUint32(4, true);
    onServerMessage(JSON.parse(new TextDecoder().decode(new Uint8Array(e.data, 8, n))));
    const t0 = performance.now();
    const bmp = await createImageBitmap(new Blob([new Uint8Array(e.data, 8 + n)], { type: "image/jpeg" }));
    push(S.dec, performance.now() - t0);
    if (outc.width !== bmp.width) { outc.width = bmp.width; outc.height = bmp.height; }
    octx.drawImage(bmp, 0, 0);
    recordShown(id);
  };
  sendConfig({ style: currentStyle() });
  // Frames in flight: enough to cover the network round trip at the GPU's
  // per-frame time, so a long RTT doesn't cap FPS. More would only queue.
  onCameraFrame(() => {
    const perFrame = Math.max(avg(S.srv.total || []) - avg(S.srv.queue || []), 33);
    // Use the measured round trip (minus GPU queueing, so this can't feed on itself).
    const trip = Math.max(S.rtt, avg(S.e2e) - avg(S.srv.queue || []));
    // No "+1": one extra frame keeps the GPU 100% busy but makes every frame
    // wait a full GPU slot in the queue. For AR, latency beats the last few FPS.
    const maxInflight = Math.min(8, Math.max(2, Math.ceil(trip / perFrame)));
    if (S.inflight >= maxInflight || ws.readyState !== 1) return;
    const id = drawCamera(false);
    if (id < 0) return;
    S.inflight++;
    const t0 = performance.now();
    capture.toBlob(async (blob) => {
      push(S.enc, performance.now() - t0);
      const buf = new Uint8Array(4 + blob.size);
      new DataView(buf.buffer).setUint32(0, id, true);
      buf.set(new Uint8Array(await blob.arrayBuffer()), 4);
      if (ws.readyState === 1) ws.send(buf); else S.inflight--;
    }, "image/jpeg", 0.8);
  });
  ws.onclose = () => { if (S.mode === "ws") S.note = "WebSocket closed"; };
}

// ---- lifecycle ----
async function startCamera() {
  cam.srcObject?.getTracks().forEach((t) => t.stop());
  cam.srcObject = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: S.facing, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false,
  });
  await cam.play();
  armCamera();
}

function stopTransport() {
  S.pc?.close(); S.ws?.close();
  S.pc = S.dc = S.ws = null; S.inflight = 0;
}

async function start() {
  S.cfg = await (await fetch("config")).json();
  S.server = S.cfg;
  S.res = +$("res").value;
  capture.width = capture.height = S.res;
  await startCamera();
  S.running = true;
  const want = $("mode").value;
  if (want !== "ws") {
    S.mode = "webrtc"; S.note = "connecting WebRTC…";
    try {
      await startWebRTC();
      // In auto mode, fall back if no restyled frame shows up in time.
      const ok = await new Promise((r) => {
        const t0 = performance.now();
        const check = () => (S.totalShown > 0 ? r(true) : performance.now() - t0 > 10000 ? r(false) : setTimeout(check, 200));
        check();
      });
      if (ok) { S.note = ""; return; }
      S.note = "WebRTC got no frames";
    } catch (e) { S.note = "WebRTC error: " + e.message; }
    if (want === "webrtc") return;
    stopTransport();
  }
  S.mode = "ws";
  await startWebSocket();
  if (S.note) S.note += " → using WebSocket";
}

function currentStyle() { return document.querySelector("#styles .on")?.dataset.style; }

$("start").onclick = async () => {
  if (S.running) { location.reload(); return; }
  $("start").textContent = "Stop";
  try { await start(); } catch (e) { S.note = "error: " + e.message; console.error(e); }
};
$("styles").onclick = (e) => {
  const style = e.target.dataset?.style;
  if (!style) return;
  document.querySelectorAll("#styles button").forEach((b) => b.classList.toggle("on", b === e.target));
  sendControl({ type: "config", style });
};
$("apply").onclick = () => {
  const prompt = $("prompt").value.trim();
  if (!prompt) return;
  document.querySelectorAll("#styles button").forEach((b) => b.classList.remove("on"));
  sendControl({ type: "config", prompt });
};
$("res").onchange = () => {
  S.res = +$("res").value;
  capture.width = capture.height = S.res;
  S.srv = {}; S.e2e = [];
  sendConfig();
};
$("strength").onchange = () => sendConfig();
$("flip").onclick = async () => {
  S.facing = S.facing === "environment" ? "user" : "environment";
  if (S.running) await startCamera();
};

// ---- HUD ----
setInterval(() => {
  if (!S.running) return;
  sendControl({ type: "ping", t: performance.now() });
}, 1000);
setInterval(() => {
  if (!S.running) return;
  S.fps = { shown: S.shown * 2, sent: S.sent * 2 }; S.shown = 0; S.sent = 0;
  const f = (k) => avg(S.srv[k] || []).toFixed(1).padStart(5);
  const e2e = avg(S.e2e), srvTotal = avg(S.srv.total || []);
  const clientMs = S.mode === "ws" ? avg(S.enc) + avg(S.dec) : 0;
  const lines = [
    `${S.mode === "ws" ? "WebSocket" : "WebRTC"} · ${S.server.engine} · ${S.res}px · ${S.server.style}`,
    `FPS   shown ${S.fps.shown.toFixed(0)}  sent ${S.fps.sent.toFixed(0)}  gpu ${(S.server.fps || 0).toFixed(0)}  drop ${S.server.dropped || 0}`,
    `E2E   ${e2e.toFixed(0)} ms   (RTT ${S.rtt.toFixed(0)} ms)`,
    `server  queue${f("queue")} decode${f("decode")}`,
    `        pre  ${f("pre")} infer ${f("infer")} post${f("post")}`,
    `        encode${f("encode")} total ${f("total")}`,
  ];
  if (S.mode === "ws") lines.push(`phone   jpeg-enc ${avg(S.enc).toFixed(1)}  jpeg-dec ${avg(S.dec).toFixed(1)}`);
  const gpu = Object.keys(S.srv).filter((k) => k.startsWith("gpu.")).map((k) => `${k.slice(4)} ${avg(S.srv[k]).toFixed(1)}`);
  if (gpu.length) lines.push(`gpu     ${gpu.join("  ")}`);
  lines.push(`network+codec+buffers ≈ ${Math.max(0, e2e - srvTotal - clientMs).toFixed(0)} ms`);
  if (S.note) lines.push(S.note);
  hud.textContent = lines.join("\n");
}, 500);
