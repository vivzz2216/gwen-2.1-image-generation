"""Native Qwen Image 2.1 workflows and a job-scoped ComfyUI client.

The image encoder's third output is an empty target latent. Native editing
therefore uses full denoising. Masked editing instead anchors sampling to an
encoded source and a noise mask; final pixel preservation belongs to the Studio.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable

from PIL import Image


UNET_NAME = "qwen_image_2.1_int8_convrot.safetensors"
CLIP_NAME = "qwen3vl_8b_int8_convrot.safetensors"
VAE_NAME = "qwen_image_2.1_vae_bf16.safetensors"
LORA_NAME = "NSFW Qwen by TheseAlpacas V2.safetensors"


class ImageGenerationError(RuntimeError):
    """A local backend request or generation failed."""


class GenerationCancelled(ImageGenerationError):
    """The owner cancelled a generation."""


@dataclass(frozen=True)
class ModeSettings:
    name: str
    label: str
    resolution: int
    steps: int
    cache_device: str
    cache_dtype: str
    tiled_decode: bool
    description: str


MODES = {
    "fast": ModeSettings("fast", "Fast", 512, 16, "off", "default", False,
                         "Quick drafts with the lowest image and cache memory."),
    "balanced": ModeSettings("balanced", "Balanced", 768, 28, "cpu", "int8", True,
                             "More detail and instruction fidelity, with a RAM-aware prefix cache."),
    "ultra": ModeSettings("ultra", "Ultra", 1024, 36, "cpu", "int8", True,
                          "Native 1 megapixel detail with more sampling and tiled decoding; slowest."),
}


def get_mode(mode: str) -> ModeSettings:
    try:
        return MODES[mode.lower()]
    except (AttributeError, KeyError) as exc:
        raise ValueError("Mode must be fast, balanced, or ultra") from exc


def mode_info() -> list[dict]:
    return [dict(asdict(mode), cfg=1.0, sampler="euler", scheduler="simple",
                 encoder_device=os.getenv("QWEN_ENCODER_DEVICE", "default"), vae_device="cuda", native_rgba=True)
            for mode in MODES.values()]


def image_dimensions(mode: str, size: tuple[int, int]) -> tuple[int, int]:
    """Match TextEncodeQwenImage21's area-preserving multiples-of-32 resize."""
    if min(size) <= 0 or max(size) / min(size) > 8:
        raise ValueError("Image aspect ratio must be between 1:8 and 8:1")
    target = get_mode(mode).resolution
    ratio = size[0] / size[1]
    return (max(32, round(math.sqrt(target * target * ratio) / 32) * 32),
            max(32, round(math.sqrt(target * target / ratio) / 32) * 32))


def generation_dimensions(mode: str = "balanced", aspect_ratio: str = "1:1") -> tuple[int, int]:
    supported = {"1:1": (1, 1), "16:9": (16, 9), "9:16": (9, 16),
                 "4:3": (4, 3), "3:4": (3, 4), "3:2": (3, 2), "2:3": (2, 3)}
    if aspect_ratio not in supported:
        raise ValueError("Unsupported aspect ratio")
    return image_dimensions(mode, supported[aspect_ratio])


def _input_name(name: str) -> str:
    # ComfyUI supports relative input subfolders, including Studio's isolated
    # uploads folder. Validate Windows and POSIX forms before normalizing.
    if not isinstance(name, str) or not name or "\x00" in name or ":" in name:
        raise ValueError("Invalid ComfyUI input path")
    normalized = name.replace("\\", "/")
    if (Path(name).is_absolute() or PureWindowsPath(name).drive or normalized.startswith("/")
            or any(part in {"", ".", ".."} for part in normalized.split("/"))):
        raise ValueError("ComfyUI input must stay inside the input folder")
    return normalized


def _node(class_type: str, **inputs) -> dict:
    return {"class_type": class_type, "inputs": inputs}


def build_workflow(
    prompt: str, mode: str = "balanced", seed: int = 42, aspect_ratio: str = "1:1",
    steps: int | None = None, source_name: str | None = None,
    source_size: tuple[int, int] | None = None, source_has_alpha: bool = False,
    reference_names: list[str] | tuple[str, ...] = (), mask_name: str | None = None,
    cache_device: str | None = None, cache_dtype: str | None = None,
    lora_name: str | None = None, lora_strength: float = 0.8,
    sampler_name: str | None = None, scheduler: str | None = None,
) -> dict:
    """Build a core-node API workflow; SaveImage is always node 9.

    Extra references provide visual guidance, not pixel locks. Up to two extra
    references are accepted to keep the sequence feasible on a 6 GB card. Masks
    are grayscale PNGs: white edits, black anchors. Source images may be RGBA.
    """
    profile = get_mode(mode)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Prompt cannot be empty")
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2**64 - 1:
        raise ValueError("Seed must be an unsigned 64-bit integer")
    steps = profile.steps if steps is None else steps
    if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= 100:
        raise ValueError("Steps must be between 1 and 100")
    if len(reference_names) > 2:
        raise ValueError("Use at most two additional references on this 6 GB configuration")
    if (mask_name or reference_names) and not source_name:
        raise ValueError("Editing references and masks require a source image")
    device = cache_device or os.getenv("QWEN_CACHE_DEVICE", profile.cache_device)
    dtype = cache_dtype or os.getenv("QWEN_CACHE_DTYPE", profile.cache_dtype)
    if device not in {"auto", "gpu", "cpu", "off"} or dtype not in {"default", "int8", "int4"}:
        raise ValueError("Invalid Qwen prefix cache settings")
    encoder_device = os.getenv("QWEN_ENCODER_DEVICE", "default")
    if encoder_device not in {"default", "cpu"}:
        raise ValueError("QWEN_ENCODER_DEVICE must be default or cpu")
    workflow = {
        "1": _node("UNETLoader", unet_name=UNET_NAME, weight_dtype="default"),
        "3": _node("CLIPLoader", clip_name=CLIP_NAME, type="qwen_image", device=encoder_device),
        "4": _node("VAELoader", vae_name=VAE_NAME),
        "5": _node("TextEncodeQwenImage21", clip=["3", 0], prompt=prompt.strip(),
                   negative_prompt="", resolution=profile.resolution),
    }
    model_link = ["1", 0]
    if lora_name and float(lora_strength) != 0.0:
        workflow["1b"] = _node("LoraLoaderModelOnly", model=model_link, lora_name=lora_name, strength_model=float(lora_strength))
        model_link = ["1b", 0]
    workflow["2"] = _node("QwenImage21Cache", model=model_link, device=device, dtype=dtype)
    if source_name:
        workflow["10"] = _node("LoadImage", image=_input_name(source_name))
        image_link = ["10", 0]
        if source_has_alpha:
            # LoadImage returns RGB and inverse alpha. The join node inverts it
            # internally; an extra InvertMask here would corrupt transparency.
            workflow["11"] = _node("JoinImageWithAlpha", image=image_link, alpha=["10", 1])
            image_link = ["11", 0]
        workflow["5"]["inputs"].update({"images.image_1": image_link, "vae": ["4", 0]})
        for index, reference in enumerate(reference_names, start=2):
            node_id = str(20 + index)
            alpha_id = str(30 + index)
            workflow[node_id] = _node("LoadImage", image=_input_name(reference))
            workflow[alpha_id] = _node("JoinImageWithAlpha", image=[node_id, 0], alpha=[node_id, 1])
            workflow["5"]["inputs"][f"images.image_{index}"] = [alpha_id, 0]
        latent_link = ["5", 2]
        if mask_name:
            if source_size is None:
                raise ValueError("Masked editing requires the source image size")
            width, height = image_dimensions(mode, source_size)
            workflow["12"] = _node("ImageScale", image=image_link, width=width, height=height,
                                   upscale_method="lanczos", crop="disabled")
            workflow["13"] = _node("VAEEncodeTiled", pixels=["12", 0], vae=["4", 0],
                                   tile_size=512, overlap=64, temporal_size=64, temporal_overlap=8)
            workflow["14"] = _node("LoadImageMask", image=_input_name(mask_name), channel="red")
            workflow["15"] = _node("SetLatentNoiseMask", samples=["13", 0], mask=["14", 0])
            latent_link = ["15", 0]
    else:
        width, height = generation_dimensions(mode, aspect_ratio)
        # KSampler converts this empty latent to the model's native 64 channels
        # and 16x downscale using fix_empty_latent_channels.
        workflow["6"] = _node("EmptyLatentImage", width=width, height=height, batch_size=1)
        latent_link = ["6", 0]
    workflow["7"] = _node("KSampler", model=["2", 0], positive=["5", 0], negative=["5", 1],
                          latent_image=latent_link, seed=seed, steps=steps, cfg=1.0,
                          sampler_name=sampler_name or "euler", scheduler=scheduler or "simple", denoise=1.0)
    if profile.tiled_decode:
        workflow["8"] = _node("VAEDecodeTiled", samples=["7", 0], vae=["4", 0],
                              tile_size=512, overlap=64, temporal_size=64, temporal_overlap=8)
    else:
        workflow["8"] = _node("VAEDecode", samples=["7", 0], vae=["4", 0])
    workflow["9"] = _node("SaveImage", images=["8", 0], filename_prefix=f"Qwen21_{profile.name}")
    return workflow


def build_generation(prompt: str, mode: str = "balanced", aspect_ratio: str = "1:1",
                     steps: int | None = None, seed: int = 42, **kwargs) -> dict:
    return build_workflow(prompt, mode, seed, aspect_ratio, steps, **kwargs)


def build_edit(prompt: str, source_name: str, mode: str = "balanced", steps: int | None = None,
               seed: int = 42, **kwargs) -> dict:
    return build_workflow(prompt, mode=mode, seed=seed, steps=steps, source_name=source_name, **kwargs)


class ComfyClient:
    """Blocking client, intended to run in Studio's single background worker."""

    def __init__(self, url: str, output_dir: Path, poll_interval: float = 1.0):
        self.url = url.rstrip("/")
        self.output_dir = Path(output_dir).resolve()
        self.poll_interval = poll_interval
        self._owned: set[str] = set()
        self._lock = threading.Lock()

    def request(self, path: str, method: str = "GET", payload: dict | None = None,
                timeout: float = 15) -> dict:
        request = urllib.request.Request(
            self.url + path, method=method,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            if not isinstance(result, dict):
                raise ImageGenerationError("ComfyUI returned an invalid response")
            return result
        except urllib.error.HTTPError as exc:
            detail = exc.read(16384).decode("utf-8", errors="replace")
            raise ImageGenerationError(f"ComfyUI HTTP {exc.code}: {detail[:1600]}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ImageGenerationError(f"Cannot reach local ComfyUI at {self.url}: {exc}") from exc

    def submit(self, workflow: dict, client_id: str | None = None) -> str:
        result = self.request("/prompt", "POST", {"prompt": workflow, "client_id": client_id or str(uuid.uuid4())})
        prompt_id = result.get("prompt_id")
        if result.get("error") or result.get("node_errors") or not prompt_id:
            raise ImageGenerationError(f"ComfyUI rejected the workflow: {json.dumps(result)[:1800]}")
        try:
            if str(uuid.UUID(prompt_id)) != prompt_id:
                raise ValueError("noncanonical prompt id")
        except (ValueError, TypeError, AttributeError) as exc:
            raise ImageGenerationError("ComfyUI returned an invalid prompt ID") from exc
        with self._lock:
            self._owned.add(prompt_id)
        return prompt_id

    def cancel(self, prompt_id: str) -> bool:
        with self._lock:
            if prompt_id not in self._owned:
                return False
        # Installed ComfyUI has atomic targeted cancellation. Never use the
        # global /interrupt endpoint or clear anyone else's queue.
        return bool(self.request(f"/api/jobs/{prompt_id}/cancel", "POST", {}).get("cancelled"))

    def wipe_history(self, prompt_id: str | None = None) -> None:
        """Immediately purge prompt data and queue history from ComfyUI."""
        try:
            if prompt_id:
                self.request("/history", "POST", {"delete": [prompt_id]})
            else:
                self.request("/history", "POST", {"clear": True})
        except Exception:
            pass

    def is_active(self, prompt_id: str) -> bool:
        """Confirm a job has released its input files; connection errors propagate."""
        queue = self.request("/queue")
        rows = queue.get("queue_running", []) + queue.get("queue_pending", [])
        return any(len(row) > 1 and row[1] == prompt_id for row in rows)

    def _output(self, record: dict) -> Path:
        status = record.get("status", {})
        if status.get("status_str") == "error":
            messages = status.get("messages", [])
            for kind, details in messages:
                if kind == "execution_interrupted":
                    raise GenerationCancelled("Generation cancelled")
                if kind == "execution_error":
                    message = str(details.get("exception_message", "Generation failed")).strip()
                    raise ImageGenerationError(message[:1800])
            raise ImageGenerationError(f"Qwen generation failed: {json.dumps(status)[:1800]}")
        images = record.get("outputs", {}).get("9", {}).get("images", [])
        if not status.get("completed") or not images:
            raise ImageGenerationError("Qwen completed without an output image")
        item = images[0]
        if item.get("type", "output") != "output":
            raise ImageGenerationError("ComfyUI returned an unexpected output type")
        filename, subfolder = item.get("filename", ""), item.get("subfolder", "")
        if not isinstance(filename, str) or not filename or not isinstance(subfolder, str):
            raise ImageGenerationError("ComfyUI returned an invalid output path")
        output = (self.output_dir / subfolder / filename).resolve()
        if not output.is_relative_to(self.output_dir) or not output.is_file():
            raise ImageGenerationError("ComfyUI returned an invalid or missing output path")
        try:
            with Image.open(output) as image:
                image.verify()
        except (OSError, ValueError) as exc:
            raise ImageGenerationError("ComfyUI output is not a valid image") from exc
        return output

    def run(self, workflow: dict, progress: Callable[[dict], None] | None = None,
            cancel_event: threading.Event | None = None, timeout: float = 1800,
            on_submitted: Callable[[str], None] | None = None) -> Path:
        cancel_event = cancel_event or threading.Event()
        if cancel_event.is_set():
            raise GenerationCancelled("Generation cancelled before submission")
        client_id = str(uuid.uuid4())
        socket = self._progress_socket(client_id) if progress else None
        try:
            prompt_id = self.submit(workflow, client_id)
        except Exception:
            if socket:
                socket.close()
            raise
        started = time.monotonic()
        current_progress: dict = {}
        released = False
        try:
            if on_submitted:
                on_submitted(prompt_id)
            while True:
                elapsed = time.monotonic() - started
                if cancel_event.is_set() or elapsed > timeout:
                    # Cancel first, then wait for the queue to release the job.
                    # Studio must keep input files until model execution stops.
                    self.cancel(prompt_id)
                    self._wait_released(prompt_id)
                    released = True
                    if cancel_event.is_set():
                        raise GenerationCancelled("Generation cancelled")
                    raise ImageGenerationError(f"Qwen generation timed out after {timeout:g} seconds")
                history = self.request(f"/history/{prompt_id}")
                if prompt_id in history:
                    released = True
                    output_file = self._output(history[prompt_id])
                    self.wipe_history(prompt_id)
                    return output_file
                queue = self.request("/queue")
                running = any(row[1] == prompt_id for row in queue.get("queue_running", []) if len(row) > 1)
                pending = queue.get("queue_pending", [])
                position = next((i + 1 for i, row in enumerate(pending) if len(row) > 1 and row[1] == prompt_id), None)
                if progress:
                    if socket:
                        try:
                            current_progress.update(self._read_progress(socket, prompt_id, current_progress))
                        except Exception:
                            socket.close()
                            socket = None  # Polling remains the completion authority.
                    update = {"stage": "running" if running else "queued", "elapsed": round(elapsed, 1),
                              "prompt_id": prompt_id, "queue_position": position}
                    if running:
                        update.update(current_progress)
                    progress(update)
                cancel_event.wait(self.poll_interval)
        finally:
            if socket:
                socket.close()
            if released:
                with self._lock:
                    self._owned.discard(prompt_id)

    def _progress_socket(self, client_id: str):
        try:
            import websocket
        except ImportError:
            return None
        url = self.url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        try:
            socket = websocket.create_connection(
                url + "/ws?clientId=" + urllib.parse.quote(client_id), timeout=3, origin=self.url,
            )
            socket.settimeout(0.02)
            return socket
        except (OSError, websocket.WebSocketException):
            return None

    @staticmethod
    def _read_progress(socket, prompt_id: str, previous: dict | None = None) -> dict:
        import websocket
        update = {}
        stage_names = {"3": "encoding", "5": "encoding", "7": "sampling",
                       "8": "decoding", "9": "saving", "13": "encoding"}
        for _ in range(100):
            try:
                raw = socket.recv()
            except websocket.WebSocketTimeoutException:
                break
            if not isinstance(raw, str):
                continue
            event = json.loads(raw)
            data = event.get("data", {})
            if data.get("prompt_id") != prompt_id:
                continue
            if event.get("type") == "executing" and data.get("node"):
                node = str(data["node"])
                update.update(stage=stage_names.get(node, "preparing"), node=node, step=None, steps=None)
            elif event.get("type") == "progress":
                node = str(data.get("node") or update.get("node") or (previous or {}).get("node") or "7")
                update.update(stage=stage_names.get(node, "running"), node=node,
                              step=data.get("value"), steps=data.get("max"))
        return update

    def _wait_released(self, prompt_id: str, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_active(prompt_id):
                return
            time.sleep(min(self.poll_interval, 1.0))
        raise ImageGenerationError("Cancellation requested; ComfyUI is still releasing the current job")
