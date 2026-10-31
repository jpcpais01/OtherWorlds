"""Restyling engines. All share one interface:

    engine.configure(prompt=..., res=..., strength=...)   # any subset
    out_rgb, timings_ms = engine.process(in_rgb)        # HxWx3 uint8 numpy

process() always returns a res x res image. Both calls must run on the same
worker thread (see Restyler), because the GPU state is not thread safe.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

STYLES = {
    "ghibli": "studio ghibli anime illustration, hand-drawn, soft watercolor, "
              "lush pastel colors, whimsical, by hayao miyazaki",
    "cyberpunk": "cyberpunk, neon lights, glowing magenta and cyan, futuristic "
                 "city, blade runner, rain, highly detailed digital art",
    "zombie": "zombie apocalypse, post-apocalyptic ruins, decay, overgrown, "
              "grim desaturated colors, horror movie still",
}

# "strength" 0..1 maps to how much noise is added to the camera frame.
# Higher = stronger style, less faithful to the scene.
def strength_to_t_index(strength):
    # StreamDiffusion uses 50 scheduler steps; lower index = more noise.
    return int(round(45 - 30 * strength))  # 0.0 -> 45, 1.0 -> 15


class FakeEngine:
    """CPU stand-in so the networking and HUD can be tested without a GPU."""

    name = "fake"

    def __init__(self, delay_ms=0):
        self.delay = delay_ms / 1000
        self.prompt, self.res, self.strength = STYLES["ghibli"], 512, 0.5

    def configure(self, prompt=None, res=None, strength=None):
        self.prompt = prompt or self.prompt
        self.res = res or self.res
        self.strength = self.strength if strength is None else strength

    def process(self, img):
        t0 = time.perf_counter()
        img = _resize(img, self.res).astype(np.float32)
        t1 = time.perf_counter()
        p = self.prompt.lower()
        if "cyberpunk" in p:
            tint = np.array([1.3, 0.6, 1.4])
        elif "zombie" in p:
            img = img.mean(axis=2, keepdims=True).repeat(3, axis=2)
            tint = np.array([0.8, 1.05, 0.7])
        else:
            tint = np.array([1.1, 1.05, 0.9])
        out = img * (1 - self.strength) + (img * tint) * self.strength
        time.sleep(self.delay)
        t2 = time.perf_counter()
        out = out.clip(0, 255).astype(np.uint8)
        t3 = time.perf_counter()
        return out, {"pre": (t1 - t0) * 1e3, "infer": (t2 - t1) * 1e3, "post": (t3 - t2) * 1e3}


class _TorchEngine:
    """Shared GPU pre/post-processing for the diffusion engines."""

    def _to_tensor(self, img):
        import torch
        import torch.nn.functional as F

        x = torch.from_numpy(img).to(self.device, non_blocking=True)
        x = x.permute(2, 0, 1).unsqueeze(0).to(self.dtype) / 255.0
        h, w = x.shape[-2:]
        s = min(h, w)  # center-crop to square, then resize
        x = x[..., (h - s) // 2:(h - s) // 2 + s, (w - s) // 2:(w - s) // 2 + s]
        if s != self.res:
            x = F.interpolate(x, size=(self.res, self.res), mode="bilinear", antialias=True)
        return x

    def _to_numpy(self, x):
        x = (x[0].float().clamp(0, 1) * 255).round().to("cpu").byte()
        return x.permute(1, 2, 0).contiguous().numpy()

    def process(self, img):
        import torch

        t0 = time.perf_counter()
        x = self._to_tensor(img)
        torch.cuda.synchronize()
        t1 = time.perf_counter()
        y = self._infer(x)
        torch.cuda.synchronize()
        t2 = time.perf_counter()
        out = self._to_numpy(y)
        t3 = time.perf_counter()
        timings = {"pre": (t1 - t0) * 1e3, "infer": (t2 - t1) * 1e3, "post": (t3 - t2) * 1e3}
        # Engines may break "infer" down further (GPU-side, shown as "gpu" in the HUD).
        return out, {**timings, **getattr(self, "split", {})}


class StreamDiffusionEngine(_TorchEngine):
    """StreamDiffusion + sd-turbo + tiny VAE, single-step by default."""

    name = "streamdiffusion"

    def __init__(self, model="stabilityai/sd-turbo"):
        import torch
        from diffusers import AutoencoderTiny, StableDiffusionPipeline

        torch.backends.cuda.matmul.allow_tf32 = True
        self.device, self.dtype = torch.device("cuda"), torch.float16
        self.pipe = StableDiffusionPipeline.from_pretrained(
            model, torch_dtype=self.dtype, variant="fp16", safety_checker=None
        ).to(self.device)
        self.taesd = AutoencoderTiny.from_pretrained("madebyollin/taesd", torch_dtype=self.dtype).to(self.device)
        self.prompt, self.res, self.strength = STYLES["ghibli"], 512, 0.5
        self._build()

    def _build(self):
        from streamdiffusion import StreamDiffusion

        # One denoising step keeps latency lowest. More entries in
        # t_index_list improve quality but add a frame of delay each.
        self.stream = StreamDiffusion(
            self.pipe,
            t_index_list=[strength_to_t_index(self.strength)],
            torch_dtype=self.dtype,
            width=self.res,
            height=self.res,
            cfg_type="none",
        )
        self.stream.vae = self.taesd
        self.stream.prepare(self.prompt, num_inference_steps=50, guidance_scale=0)
        warm = np.zeros((self.res, self.res, 3), np.uint8)
        for _ in range(3):
            self.process(warm)

    def configure(self, prompt=None, res=None, strength=None):
        rebuild = (res and res != self.res) or (strength is not None and strength != self.strength)
        self.res = res or self.res
        self.strength = self.strength if strength is None else strength
        if prompt and prompt != self.prompt:
            self.prompt = prompt
            if not rebuild:
                self.stream.update_prompt(prompt)
        if rebuild:
            self._build()

    def _infer(self, x):
        y = self.stream(x)  # takes [0,1], returns [-1,1]
        return y / 2 + 0.5


class DiffusersEngine(_TorchEngine):
    """Plain diffusers sd-turbo img2img. Slower; used if StreamDiffusion fails to import."""

    name = "diffusers"

    def __init__(self, model="stabilityai/sd-turbo"):
        import torch
        from diffusers import AutoencoderTiny, AutoPipelineForImage2Image

        self.device, self.dtype = torch.device("cuda"), torch.float16
        self.pipe = AutoPipelineForImage2Image.from_pretrained(
            model, torch_dtype=self.dtype, variant="fp16", safety_checker=None
        ).to(self.device)
        self.pipe.vae = AutoencoderTiny.from_pretrained("madebyollin/taesd", torch_dtype=self.dtype).to(self.device)
        self.pipe.set_progress_bar_config(disable=True)
        self.prompt, self.res, self.strength = STYLES["ghibli"], 512, 0.5

    def configure(self, prompt=None, res=None, strength=None):
        self.prompt = prompt or self.prompt
        self.res = res or self.res
        self.strength = self.strength if strength is None else strength

    def _infer(self, x):
        import torch

        s = min(max(self.strength, 0.1), 1.0)
        steps = max(1, int(np.ceil(1 / s)))  # exactly one effective denoising step
        with torch.no_grad():
            return self.pipe(
                self.prompt, image=x, strength=s, num_inference_steps=steps,
                guidance_scale=0.0, output_type="pt",
            ).images


class SDXLTurboEngine(_TorchEngine):
    """SDXL-Turbo, one denoising step, guided by a Canny-edge ControlNet.

    A hand-written single-step loop (no diffusers pipeline) so nothing but the
    networks runs per frame. The ControlNet keeps the room's shapes in place,
    which lets strength go high (strong style) without losing the scene, and
    the fixed noise tensor keeps frames from flickering more than they must.
    """

    name = "sdxl-turbo"
    # SDXL-Turbo was distilled for these timesteps (4-step schedule).
    TIMESTEPS = (249, 499, 749, 999)

    def __init__(self, model="stabilityai/sdxl-turbo",
                 controlnet="diffusers/controlnet-canny-sdxl-1.0-small", vae="madebyollin/taesdxl"):
        import torch
        from diffusers import AutoencoderTiny, ControlNetModel, StableDiffusionXLPipeline

        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
        device, dtype = torch.device("cuda"), torch.float16
        pipe = StableDiffusionXLPipeline.from_pretrained(
            model, torch_dtype=dtype, variant="fp16",
            vae=AutoencoderTiny.from_pretrained(vae, torch_dtype=dtype),
        ).to(device)
        cn = ControlNetModel.from_pretrained(controlnet, torch_dtype=dtype).to(device)
        self._setup(pipe, cn, device, dtype)

    def _setup(self, pipe, controlnet, device, dtype):
        import torch

        self.pipe, self.controlnet, self.device, self.dtype = pipe, controlnet, device, dtype
        self.unet, self.vae = pipe.unet, pipe.vae
        self.alphas = pipe.scheduler.alphas_cumprod.to(device)
        self.cn_scale = float(os.environ.get("CN_SCALE", 0.6))  # how strictly to follow the edges
        self.compiled = os.environ.get("COMPILE") == "1"
        if self.compiled:
            # At 512px the UNet is limited by per-kernel launch overhead, not
            # math; CUDA graphs ("reduce-overhead") remove most of it. Costs a
            # 1-3 min compile at startup and again for each new resolution.
            print("compiling UNet + ControlNet (1-3 min)...")
            self.unet = torch.compile(self.unet, mode="reduce-overhead")
            self.controlnet = torch.compile(self.controlnet, mode="reduce-overhead")
        self.prompt, self.res, self.strength = STYLES["ghibli"], 512, 0.5
        self.embeds, self.noise = {}, None
        self._encode_prompt(self.prompt)
        warm = np.zeros((self.res, self.res, 3), np.uint8)
        for _ in range(3):
            self.process(warm)
        torch.cuda.synchronize()

    def _encode_prompt(self, prompt):
        import torch

        if prompt not in self.embeds:
            with torch.no_grad():
                pe, _, pooled, _ = self.pipe.encode_prompt(prompt, device=self.device, do_classifier_free_guidance=False)
            self.embeds[prompt] = (pe, pooled)
        return self.embeds[prompt]

    def configure(self, prompt=None, res=None, strength=None):
        if prompt:
            self._encode_prompt(prompt)
            self.prompt = prompt
        if res and res != self.res:
            self.res, self.noise = res, None
        if strength is not None:
            self.strength = strength

    def _to_tensor(self, img):
        import cv2
        import torch

        x = super()._to_tensor(img)
        # Canny edges on the CPU from the (already resized) frame: ~1 ms at 512px.
        small = (x[0].permute(1, 2, 0).float() * 255).byte().cpu().numpy()
        edges = cv2.Canny(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), 100, 200)
        self.edges = torch.from_numpy(edges).to(self.device).to(self.dtype).div(255).expand(1, 3, -1, -1)
        return x

    def _infer(self, x):
        import torch

        t = min(self.TIMESTEPS, key=lambda v: abs(v - self.strength * 1000))
        a = self.alphas[t].to(self.dtype)
        pe, pooled = self.embeds[self.prompt]
        r = self.res
        cond = {"text_embeds": pooled,
                "time_ids": torch.tensor([[r, r, 0, 0, r, r]], device=self.device, dtype=self.dtype)}
        ev = [torch.cuda.Event(enable_timing=True) for _ in range(5)]
        with torch.no_grad():
            ev[0].record()
            latents = self.vae.encode(x * 2 - 1).latents * self.vae.config.scaling_factor
            if self.noise is None or self.noise.shape != latents.shape:
                g = torch.Generator(self.device).manual_seed(0)
                self.noise = torch.randn(latents.shape, generator=g, device=self.device, dtype=self.dtype)
            x_t = a.sqrt() * latents + (1 - a).sqrt() * self.noise
            ev[1].record()
            if self.compiled:
                torch.compiler.cudagraph_mark_step_begin()
            down, mid = self.controlnet(
                x_t, t, encoder_hidden_states=pe, controlnet_cond=self.edges,
                conditioning_scale=self.cn_scale, added_cond_kwargs=cond, return_dict=False,
            )
            if self.compiled:
                # CUDA-graph outputs live in reused buffers; copy before the next graph runs.
                down, mid = [d.clone() for d in down], mid.clone()
            ev[2].record()
            eps = self.unet(
                x_t, t, encoder_hidden_states=pe, added_cond_kwargs=cond,
                down_block_additional_residuals=down, mid_block_additional_residual=mid, return_dict=False,
            )[0]
            if self.compiled:
                eps = eps.clone()
            ev[3].record()
            x0 = (x_t - (1 - a).sqrt() * eps) / a.sqrt()
            y = self.vae.decode(x0 / self.vae.config.scaling_factor).sample
            ev[4].record()
        torch.cuda.synchronize()
        self.split = {f"gpu.{k}": ev[i].elapsed_time(ev[i + 1])
                      for i, k in enumerate(("vae_enc", "controlnet", "unet", "vae_dec"))}
        return y / 2 + 0.5


def make_engine(backend):
    if backend == "fake":
        return FakeEngine()
    if backend in ("auto", "sdxl"):
        return SDXLTurboEngine()
    if backend == "streamdiffusion":
        try:
            return StreamDiffusionEngine()
        except ImportError as e:
            print(f"StreamDiffusion unavailable ({e}); falling back to diffusers")
    return DiffusersEngine()


class Restyler:
    """Owns the engine and the single GPU worker thread."""

    def __init__(self, backend):
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.engine = self.pool.submit(make_engine, backend).result()
        self.state = {"style": "ghibli", "prompt": STYLES["ghibli"], "res": 512, "strength": 0.5}
        print(f"engine ready: {self.engine.name}")

    async def run(self, loop, fn, *args):
        return await loop.run_in_executor(self.pool, fn, *args)

    async def process(self, loop, img):
        return await self.run(loop, self.engine.process, img)

    async def configure(self, loop, msg):
        """msg: {style?, prompt?, res?, strength?} from a client."""
        prompt = None
        if msg.get("prompt"):
            prompt, self.state["style"] = msg["prompt"], "custom"
        elif msg.get("style") in STYLES:
            prompt, self.state["style"] = STYLES[msg["style"]], msg["style"]
        res = int(msg["res"]) if msg.get("res") else None
        if res:
            res = max(256, min(768, res // 64 * 64))
        strength = float(msg["strength"]) if msg.get("strength") is not None else None
        await self.run(loop, lambda: self.engine.configure(prompt=prompt, res=res, strength=strength))
        if prompt:
            self.state["prompt"] = prompt
        if res:
            self.state["res"] = res
        if strength is not None:
            self.state["strength"] = strength
        return dict(self.state, engine=self.engine.name)


def _resize(img, res):
    """Nearest-neighbour center-crop + resize (fake engine only)."""
    h, w = img.shape[:2]
    s = min(h, w)
    img = img[(h - s) // 2:(h - s) // 2 + s, (w - s) // 2:(w - s) // 2 + s]
    idx = (np.arange(res) * s / res).astype(int)
    return img[idx][:, idx]
