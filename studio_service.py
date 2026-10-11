"""Persistent local Qwen jobs and region-preserving edit orchestration."""
import asyncio
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, PngImagePlugin
from pydantic import BaseModel, Field, field_validator

import psutil
import qwen_engine as engine
import editing_regions as regions
import dolphin_prompting as dolphin
from model_sessions import ModelSessions
import prompt_intent
import pose_prompting
import pose_assist

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = str(PROJECT_DIR / "outputs")
COMFY_DIR = PROJECT_DIR / "ComfyUI_windows_portable" / "ComfyUI"
COMFY_INPUT_DIR = COMFY_DIR / "input" / "studio_uploads"
COMFY_OUTPUT_DIR = COMFY_DIR / "output"
COMFY_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
STATE_DIR = Path(OUTPUT_DIR) / ".studio"
STATE_DIR.mkdir(parents=True, exist_ok=True)
COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = STATE_DIR / "jobs.sqlite3"
CLIENT = engine.ComfyClient(COMFY_URL, COMFY_OUTPUT_DIR)
RESOURCE_LOCK = threading.Lock()
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qwen-studio")
CANCEL_EVENTS = {}
STATE_LOCK = threading.RLock()


ACTIVE_PROMPTS: dict[str, str] = {}
MODEL_SESSIONS = ModelSessions()
MODEL_REAPER_STOP = threading.Event()
MODEL_REAPER_LOCK = threading.Lock()
MODEL_REAPER_THREAD = None


@contextmanager
def connection():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL)")
    try:
        with db:
            yield db
    finally:
        db.close()


def sanitize_job_for_storage(job: dict) -> dict:
    """Ensure user prompts and compiled instructions are never stored on disk."""
    sanitized = json.loads(json.dumps(job))
    if "request" in sanitized and isinstance(sanitized["request"], dict):
        sanitized["request"]["prompt"] = "[Protected]"
    sanitized["prompt"] = "[Protected]"
    if "result" in sanitized and isinstance(sanitized["result"], dict):
        if "metadata" in sanitized["result"] and isinstance(sanitized["result"]["metadata"], dict):
            sanitized["result"]["metadata"].pop("compiled_prompt", None)
    return sanitized


def save_job(job):
    sanitized = sanitize_job_for_storage(job)
    with STATE_LOCK, connection() as db:
        db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?)",
                   (job["job_id"], json.dumps(sanitized), job["created_at"]))


def save_new_job(job):
    # Reserve a repair atomically so two concurrent submissions cannot both
    # consume the same one-repair budget after asynchronous file preparation.
    parent_id = job["request"].get("repair_parent_id")
    with STATE_LOCK:
        if parent_id:
            with connection() as db:
                rows = db.execute("SELECT data FROM jobs").fetchall()
            for row in rows:
                other = json.loads(row[0])
                if other.get("request", {}).get("repair_parent_id") == parent_id and other["status"] not in {"failed", "cancelled"}:
                    raise HTTPException(409, "This result already has a repair job")
        save_job(job)


def get_job(job_id):
    with connection() as db:
        row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Job not found")
    return json.loads(row[0])


def update_job(job_id, **changes):
    with STATE_LOCK:
        job = get_job(job_id)
        job.update(changes)
        save_job(job)
        return job


def save_batch(batch):
    with STATE_LOCK, connection() as db:
        db.execute("INSERT OR REPLACE INTO batches VALUES (?, ?, ?)",
                   (batch["batch_id"], json.dumps(batch), batch["created_at"]))


def get_batch(batch_id):
    with connection() as db:
        row = db.execute("SELECT data FROM batches WHERE id=?", (batch_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Batch not found")
    return json.loads(row[0])


def update_batch(batch_id, **changes):
    with STATE_LOCK:
        batch = get_batch(batch_id)
        batch.update(changes)
        save_batch(batch)
        return batch


@asynccontextmanager
async def lifespan(app):
    # Proactively wipe any residual prompt files and ComfyUI queue history on startup
    try:
        CLIENT.wipe_history()
    except Exception:
        pass
    for path in Path(COMFY_OUTPUT_DIR).glob("*.png"):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    for path in Path(COMFY_INPUT_DIR).glob("*.*"):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    for path in Path(STATE_DIR).glob("*.workflow.json"):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    with connection() as db:
        rows = db.execute("SELECT data FROM jobs").fetchall()
    for row in rows:
        job = json.loads(row[0])
        if job["status"] in ("queued", "running", "cancelling"):
            job.update(status="failed", error="Studio restarted during this job. Run it again.", finished_at=time.time())
            save_job(job)
    try:
        yield
    finally:
        MODEL_REAPER_STOP.set()


app = FastAPI(title="Qwen Image 2.1 Studio", version="3.0.0", lifespan=lifespan)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    aspect_ratio: Literal["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"] = "1:1"
    mode: Literal["fast", "balanced", "ultra"] = "balanced"
    is_transparent: bool = False
    num_steps: Optional[int] = Field(default=None, ge=1, le=100)
    seed: int = Field(default=42, ge=0, le=2**63-1)
    enhance_prompt: bool = False
    lora_enabled: bool = True
    lora_strength: float = Field(default=0.8, ge=0.0, le=2.0)

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Enter a prompt")
        return value.strip()


class EnhanceRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    task: Literal["generate", "edit"] = "generate"
    aspect_ratio: Literal["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"] = "1:1"
    region: str = Field(default="auto", max_length=80)
    preserve: bool = True
    reference_count: int = Field(default=0, ge=0, le=2)
    is_transparent: bool = False
    model: Optional[Literal["dolphin", "qwen", "qwen21-dolphin", "qwen3.5:4b-q4_K_M", "qwen21-qwen4b", "qwen27b"]] = None
    enhancement_style: Literal["describe", "polish"] = "describe"
    pose_assist: bool = False

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Enter a prompt first")
        return value.strip()


ImageGenerationError = engine.ImageGenerationError


def open_image(data):
    if not data or len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Choose an image smaller than 25 MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("PNG", "JPEG", "WEBP"):
                raise ValueError("Use PNG, JPEG, or WebP")
            if image.width * image.height > 24_000_000 or max(image.size) > 8192:
                raise ValueError("Use an image up to 24 megapixels and 8192 pixels per side")
            image.load()
            oriented = ImageOps.exif_transpose(image)
            return oriented.convert("RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, f"Cannot open that image: {exc}") from exc


def save_image(image, label):
    filename = f"{label}_{uuid.uuid4().hex}.png"
    path = Path(OUTPUT_DIR) / filename
    clean = image.copy()
    clean.info.clear()
    clean.save(path, format="PNG")
    return path, f"/outputs/{filename}"


def compile_prompt(instruction, target="global", preserve=True, transparent=False, reference=False, clean_prompt=False, pose_enabled=False, pose_order="same", task="edit", repair_kind="none"):
    text = instruction.strip()
    if pose_enabled:
        text = pose_assist.compile_pose(text, task=task, pose_slot=2,
            appearance_slots=tuple(range(3, int(reference) + 2)), order=pose_order)
        return ("Produce native RGBA output with a transparent background. " if transparent else "") + text
    intent = prompt_intent.analyze(text)
    if not clean_prompt:
        action_notes = prompt_intent.guidance(text)
        if action_notes and "Depict the requested moment of contact" not in text:
            text += "\n" + action_notes
    # Soft pose guidance also applies on the direct/clean generation path.
    # Keep the entered instruction intact; no rewrite or extra model call.
    if not preserve or intent["changes_pose"]:
        text = pose_prompting.apply(text)
    if preserve:
        protected = ["the person's identity and recognizable facial features", "expression", "pose and body proportions",
                     "hand positions", "camera framing and perspective", "lighting and shadows"]
        if intent["changes_pose"] or repair_kind in {"leg", "contact"}:
            protected.remove("pose and body proportions")
            protected.remove("hand positions")
            protected.append("unrequested body proportions")
        elif repair_kind == "hand":
            protected.remove("hand positions")
        if target != "background":
            protected.append("background and surroundings")
        if target != "hair":
            protected.append("hairstyle")
        canvas = "<image1>" if reference else "the image"
        text = (f"Edit {canvas} according to this instruction: {text}\n"
                f"Change only the requested {target if target not in ('manual', 'global') else 'elements'}. "
                "Keep all unmentioned elements unchanged. Except for attributes explicitly changed by the instruction, preserve " + ", ".join(protected) + ". "
                "For unrequested attributes, match the original material lighting, perspective, scale and occlusion. "
                "Apply the requested change clearly while retaining unrelated details.")
    if reference:
        tags = ", ".join(f"<image{number}>" for number in range(2, int(reference) + 2))
        text += f" Use {tags} only to guide the requested appearance changes. <image1> is the source canvas; retain its unrequested attributes."
    if transparent:
        text = f"This is an RGBA image with transparency. {text} The image has an alpha channel and the background is transparent."
    return text


import urllib.request

OLLAMA_URL = dolphin.OLLAMA_URL


def release_qwen_for_enhancer(clear_cache=False):
    # ComfyUI /free succeeds with an empty body, unlike its JSON endpoints.
    request = urllib.request.Request(COMFY_URL + "/free", method="POST",
        data=json.dumps({"unload_models": True, "free_memory": clear_cache}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
    except OSError as exc:
        raise ImageGenerationError("ComfyUI could not release model memory") from exc


def has_active_studio_jobs():
    with connection() as db:
        return any(json.loads(row[0]).get("status") in ("queued", "running", "cancelling")
                   for row in db.execute("SELECT data FROM jobs"))


def release_idle_models():
    if not RESOURCE_LOCK.acquire(blocking=False):
        return False
    try:
        if has_active_studio_jobs():
            return False
        queue = CLIENT.request("/queue", timeout=3)
        if queue.get("queue_running") or queue.get("queue_pending"):
            return False
        dolphin.unload()
        if dolphin.MODEL != "qwen21-dolphin":
            dolphin.unload("qwen21-dolphin")
        if dolphin.MODEL != "qwen21-qwen4b":
            dolphin.unload("qwen21-qwen4b")
        release_qwen_for_enhancer(clear_cache=True)
        return True
    except (ImageGenerationError, dolphin.EnhancementError):
        return False  # Retry once idle/reachable; never interrupt an active job.
    finally:
        RESOURCE_LOCK.release()


def start_model_reaper():
    global MODEL_REAPER_THREAD
    with MODEL_REAPER_LOCK:
        if MODEL_REAPER_THREAD is not None and MODEL_REAPER_THREAD.is_alive():
            return
        MODEL_REAPER_STOP.clear()
        def watch():
            while not MODEL_REAPER_STOP.wait(5):
                MODEL_SESSIONS.release_if_due(release_idle_models)
        MODEL_REAPER_THREAD = threading.Thread(target=watch, name="studio-model-cache", daemon=True)
        MODEL_REAPER_THREAD.start()


def prepare_qwen_memory():
    # Caller holds RESOURCE_LOCK. Keep Qwen cached; prompt enhancers must yield their
    # GPU allocation before diffusion on this 6 GB card.
    MODEL_SESSIONS.activity()
    start_model_reaper()
    dolphin.unload()
    if dolphin.MODEL != "qwen21-dolphin":
        dolphin.unload("qwen21-dolphin")
    if dolphin.MODEL != "qwen21-qwen4b":
        dolphin.unload("qwen21-qwen4b")


def enhance_for_studio(prompt, **settings):
    if not RESOURCE_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Wait for the current image or mask job to finish before enhancing.")
    try:
        MODEL_SESSIONS.activity()
        start_model_reaper()
        # Move idle Qwen weights off GPU while retaining its execution cache.
        # Consecutive Enhance calls may keep Dolphin warm until image generation.
        try:
            queue = CLIENT.request("/queue", timeout=3)
        except ImageGenerationError:
            queue = None
        if queue is not None:
            if queue.get("queue_running") or queue.get("queue_pending"):
                raise HTTPException(409, "ComfyUI is busy. Enhance after its image jobs finish.")
            release_qwen_for_enhancer(clear_cache=not MODEL_SESSIONS.has_tabs())
        raw_model = settings.pop("model", None)
        selected = dolphin.resolve_model(raw_model) if raw_model else dolphin.MODEL
        testing_override = selected != dolphin.MODEL
        if testing_override:
            dolphin.unload()  # Release only the Studio-owned default enhancer.
        return dolphin.enhance(prompt, keep_alive=0 if testing_override else (-1 if MODEL_SESSIONS.has_tabs() else 0), model=selected, **settings)
    except dolphin.EnhancementError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except ImageGenerationError as exc:
        raise HTTPException(503, "Cannot release Qwen memory for Dolphin. Try again after ComfyUI is idle.") from exc
    finally:
        RESOURCE_LOCK.release()


def query_ollama_enhance(prompt: str) -> str:
    return enhance_for_studio(prompt)["enhanced"]


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((PROJECT_DIR / "web" / "index.html").read_text(encoding="utf-8"))


class BrowserSession(BaseModel):
    session_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    hidden: bool = False


@app.post("/api/session/ping")
def browser_heartbeat(req: BrowserSession):
    start_model_reaper()
    return MODEL_SESSIONS.heartbeat(req.session_id, req.hidden)


@app.post("/api/session/close")
def browser_closed(req: BrowserSession):
    return MODEL_SESSIONS.close(req.session_id)


@app.get("/api/session/status")
def browser_session_status():
    return MODEL_SESSIONS.snapshot()


@app.get("/api/capabilities")
def capabilities():
    try:
        stats = CLIENT.request("/system_stats", timeout=1.5)
        backend = {"status": "ready", "gpu": stats.get("devices", [{}])[0].get("name", "CUDA"),
                   "ram_free": stats.get("system", {}).get("ram_free"), "url": COMFY_URL}
    except Exception:
        backend = {"status": "offline", "url": COMFY_URL, "message": "Start run_qwen_comfyui.bat"}

    enhancer = {
        "status": "offline",
        "model": dolphin.MODEL,
        "installed": False,
        "url": OLLAMA_URL,
        "active_enhancer": "qwen" if "qwen" in dolphin.MODEL else "dolphin",
        "models": {
            "dolphin": {"name": "qwen21-dolphin", "installed": False, "label": "Dolphin"},
            "qwen": {"name": "qwen21-qwen4b", "installed": False, "label": "Qwen"},
        }
    }
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=1) as resp:
            if resp.status == 200:
                models = [m.get("name", "") for m in json.loads(resp.read().decode("utf-8")).get("models", [])]
                has_dolphin = any(m == "qwen21-dolphin" or m.startswith("qwen21-dolphin:") or m == "dolphin-llama3" or m.startswith("dolphin-llama3:") for m in models)
                has_qwen = any(
                    m in ("qwen21-qwen4b", "qwen3.5:4b-q4_K_M", "qwen27b")
                    or m.startswith(("qwen21-qwen4b:", "qwen3.5:4b-q4_K_M:", "qwen27b:", "qwen2.5:", "qwen3.5:"))
                    for m in models
                )
                has_current = any(m == dolphin.MODEL or m.startswith(dolphin.MODEL + ":") for m in models)
                enhancer["models"]["dolphin"]["installed"] = has_dolphin
                enhancer["models"]["qwen"]["installed"] = has_qwen
                enhancer["installed"] = has_current or has_dolphin or has_qwen
                enhancer["status"] = "ready" if enhancer["installed"] else "model_missing"
    except Exception:
        pass

    enhancer.update(profile=dolphin.PROFILE_VERSION, image_understanding=False,
                    detail_profile=dolphin.DETAIL_PROFILE,
                    intent_checks="actions, body regions and anatomical left/right; lexical only")
    lora_file = Path("ComfyUI_windows_portable/ComfyUI/models/loras") / engine.LORA_NAME
    lora_status = {
        "installed": lora_file.exists(),
        "name": engine.LORA_NAME,
        "recommended_strength": 0.8,
        "candidate": "TheseAlpacas V2",
    }
    return {"modes": engine.mode_info(), "backend": backend, "enhancer": enhancer, "segmentation": regions.segmenter_status(),
            "pose_assist": pose_assist.capabilities(),
            "max_reference_images": 3, "max_upload_mb": 25, "lora": lora_status,
            "pose_guidance": {"enabled": pose_prompting.enabled(), "scope": "soft human-pose prompting; rendered anatomy is not verified"}}


@app.post("/api/enhance")
def enhance_endpoint(req: EnhanceRequest):
    return enhance_for_studio(**req.model_dump())


@app.post("/api/intent")
def interpret_endpoint(req: EnhanceRequest):
    return pose_assist.interpretation(req.prompt)


@app.post("/api/mask")
async def mask_endpoint(image: UploadFile = File(...), prompt: str = Form(...), region: str = Form("auto")):
    source = open_image(await image.read(25 * 1024 * 1024 + 1))
    def analyze():
        with RESOURCE_LOCK:
            return regions.propose_mask(source, prompt, target=region)
    try:
        proposal = await asyncio.to_thread(analyze)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    _, source_url = save_image(source, "source")
    _, mask_url = save_image(proposal.mask, "mask")
    return {"source_url": source_url, "mask_url": mask_url, "category": proposal.target,
            "confidence": proposal.confidence, "method": proposal.method, "warnings": proposal.warnings,
            "message": "Review the highlighted region before applying the change."}


def _run_comfy_workflow(nodes):
    prepare_qwen_memory()
    output = CLIENT.run(nodes)
    filename = f"qwen_{uuid.uuid4().hex}.png"
    target_path = Path(OUTPUT_DIR) / filename
    try:
        with Image.open(output) as im:
            clean_im = im.copy()
            clean_im.info.clear()
            clean_im.save(target_path, format="PNG")
    finally:
        try:
            output.unlink(missing_ok=True)
        except Exception:
            pass
        CLIENT.wipe_history()
    return f"/outputs/{filename}"


def generate_live_ai_image(prompt, aspect_ratio="1:1", seed=42, enhance=False, num_steps=None, mode="balanced", lora_enabled=True, lora_strength=0.8):
    lora = engine.LORA_NAME if lora_enabled else None
    return _run_comfy_workflow(engine.build_workflow(prompt, mode=mode, seed=seed, aspect_ratio=aspect_ratio, steps=num_steps, lora_name=lora, lora_strength=lora_strength))


@app.post("/api/generate")
async def generate_endpoint(req: GenerateRequest):
    instruction, aspect_ratio = req.prompt, req.aspect_ratio
    if req.enhance_prompt:
        enhanced = await asyncio.to_thread(enhance_for_studio, instruction,
            aspect_ratio=aspect_ratio, is_transparent=req.is_transparent)
        instruction, aspect_ratio = enhanced["enhanced"], enhanced["wh_ratio"]
    prompt = compile_prompt(instruction, preserve=False, transparent=req.is_transparent, clean_prompt=req.lora_enabled)
    def run():
        with RESOURCE_LOCK:
            return generate_live_ai_image(prompt, aspect_ratio, req.seed, False, req.num_steps, req.mode,
                                          lora_enabled=req.lora_enabled, lora_strength=req.lora_strength)
    try:
        url = await asyncio.to_thread(run)
    except ImageGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"status": "success", "image_url": url, "mode": req.mode, "aspect_ratio": aspect_ratio}


async def prepare_request(task, prompt, mode, aspect_ratio, seed, num_steps, transparent, preserve,
                          region, image, mask, reference, references,
                          lora_enabled: bool = True, lora_strength: float = 0.8,
                          pose_enabled=False, pose_reference=None, pose_reviewed=False, pose_order="same",
                          mask_reviewed=False, repair_kind="none", repair_parent_id=""):
    settings = GenerateRequest(prompt=prompt, mode=mode, aspect_ratio=aspect_ratio, seed=seed,
                               num_steps=num_steps, is_transparent=transparent,
                               lora_enabled=lora_enabled, lora_strength=lora_strength)
    if task not in ("generate", "edit"):
        raise HTTPException(422, "Choose Generate or Edit")
    if task == "edit" and not image:
        raise HTTPException(422, "Choose a source image to edit")
    if task == "generate" and pose_enabled and image:
        raise HTTPException(422, "Choose Edit to use an original or identity image with Pose Assist")
    if pose_order not in {"same", "swap"} or repair_kind not in pose_assist.REPAIR_KINDS:
        raise HTTPException(422, "Choose supported pose assignments and repair type")
    if pose_enabled and (not pose_reference or not pose_reviewed):
        raise HTTPException(422, "Upload and approve a pose reference preview before using Pose Assist")
    if pose_enabled and mask is not None and not mask_reviewed:
        raise HTTPException(422, "Review the movement mask, including moved limbs and exposed background")
    if repair_kind != "none":
        if task != "edit" or not preserve or mask is None or not mask_reviewed or region != "manual":
            raise HTTPException(422, "A repair requires Edit mode, protected pixels and an approved custom mask with joint/contact context")
        if not re.fullmatch(r"[0-9a-f]{32}", repair_parent_id):
            raise HTTPException(422, "Choose a completed Studio result to repair")
        parent = get_job(repair_parent_id)
        if parent["status"] != "succeeded" or parent.get("request", {}).get("repair_kind", "none") != "none":
            raise HTTPException(422, "Repair a completed original result; automatic repair chains are not supported")
        with connection() as db:
            rows = db.execute("SELECT data FROM jobs").fetchall()
        for row in rows:
            other = json.loads(row[0])
            if other.get("request", {}).get("repair_parent_id") == repair_parent_id and other["status"] not in {"failed", "cancelled"}:
                raise HTTPException(409, "This result already has a repair job. Review it instead of submitting duplicate repairs")
    if transparent and task == "edit" and preserve and region != "global":
        raise HTTPException(422, "To remove the background choose a global edit. Protected edits retain original pixels outside their masks.")
    # Garment segmentation cannot make a new limb pose or person interaction.
    # Global editing still receives identity/unrequested-attribute instructions,
    # but does not promise exact original pixels outside an unrelated mask.
    motion = task == "edit" and (pose_enabled or prompt_intent.analyze(prompt)["changes_pose"])
    if motion and preserve and region != "global" and mask is None:
        if region == "auto":
            region = "global"
        else:
            raise HTTPException(422, "This action changes pose or contact. Choose Whole image (Global edit), or paint a mask covering all moved limbs, contact regions and exposed background. A clothes/accessory mask cannot perform this action safely.")
    source = open_image(await image.read(25 * 1024 * 1024 + 1)) if image else None
    if repair_kind != "none":
        parent_path = Path(OUTPUT_DIR) / f"qwen_{repair_parent_id}.png"
        if not parent_path.is_file():
            raise HTTPException(404, "The selected repair result is no longer available")
        with Image.open(parent_path) as parent_image:
            if parent_image.convert("RGBA").size != source.size or parent_image.convert("RGBA").tobytes() != source.convert("RGBA").tobytes():
                raise HTTPException(422, "Repair source must be the selected result, unchanged")
    source_path = source_url = mask_path = mask_url = None
    if source:
        source_path, source_url = save_image(source, "source")
    elif pose_enabled:
        width, height = engine.generation_dimensions(mode, aspect_ratio)
        source_path, _ = save_image(Image.new("RGB", (width, height), "white"), "pose_canvas")
        preserve = False
    target = region
    if task == "edit" and preserve and region != "global":
        if mask is not None:
            raw = open_image(await mask.read(25 * 1024 * 1024 + 1))
            try:
                selected = regions.normalize_manual_mask(raw, source.size)
                target = regions.detect_edit_target(prompt, target=region) if region != "manual" else "manual"
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            mask_path, mask_url = save_image(selected, "mask")
        else:
            # AUTOMATIC MASKING: First priority. Automatically detect area from prompt and source image.
            try:
                with RESOURCE_LOCK:
                    proposal = regions.propose_mask(source, prompt, target=region)
                selected = proposal.mask
                target = proposal.target
                mask_path, mask_url = save_image(selected, "mask")
            except Exception as exc:
                # If auto-segmentation cannot determine region or fails, fallback to global edit
                try:
                    target = regions.detect_edit_target(prompt, target=region) if region != "manual" else "manual"
                except Exception:
                    target = "global"
                target = "global"
                preserve = False
    refs = ([reference] if reference else []) + (references or [])
    if len(refs) > 2:
        raise HTTPException(422, "Use up to two extra reference images on this 6 GB GPU")
    if refs and task != "edit":
        raise HTTPException(422, "Additional references are available in Edit mode")
    reference_paths = []
    if pose_enabled:
        pose_image = open_image(await pose_reference.read(25 * 1024 * 1024 + 1))
        pose_path, _ = save_image(pose_image, "pose_reference")
        reference_paths.append(str(pose_path))
        if len(refs) > 1:
            raise HTTPException(422, "Pose Assist allows one appearance reference plus one pose reference on this 6 GB GPU")
    for ref in refs:
        normalized = open_image(await ref.read(25 * 1024 * 1024 + 1))
        ref_path, _ = save_image(normalized, "reference")
        reference_paths.append(str(ref_path))
    return {"task": task, "prompt": settings.prompt, "mode": mode, "aspect_ratio": aspect_ratio,
            "seed": seed, "num_steps": num_steps, "transparent": transparent, "preserve": preserve,
            "region": target, "source_path": str(source_path) if source_path else None, "source_url": source_url,
            "mask_path": str(mask_path) if mask_path else None, "mask_url": mask_url, "reference_paths": reference_paths,
            "lora_enabled": lora_enabled, "lora_strength": lora_strength,
            "pose_enabled": pose_enabled, "pose_reviewed": pose_reviewed, "pose_order": pose_order,
            "repair_kind": repair_kind, "repair_parent_id": repair_parent_id}


@app.post("/api/jobs", status_code=202)
async def submit_job(
    task: str = Form("generate"), prompt: str = Form(..., min_length=1, max_length=4000),
    mode: Literal["fast", "balanced", "ultra"] = Form("balanced"),
    aspect_ratio: Literal["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"] = Form("1:1"),
    seed: int = Form(42, ge=0, le=2**63-1), num_steps: Optional[int] = Form(None, ge=1, le=100),
    is_transparent: bool = Form(False), preserve: bool = Form(True), region: str = Form("auto"),
    image: Optional[UploadFile] = File(None), mask: Optional[UploadFile] = File(None),
    reference: Optional[UploadFile] = File(None), references: Optional[list[UploadFile]] = File(None),
    lora_enabled: bool = Form(True), lora_strength: float = Form(0.8),
    pose_enabled: bool = Form(False), pose_reference: Optional[UploadFile] = File(None),
    pose_reviewed: bool = Form(False), pose_order: Literal["same", "swap"] = Form("same"),
    mask_reviewed: bool = Form(False), repair_kind: Literal["none", "hand", "leg", "contact"] = Form("none"),
    repair_parent_id: str = Form("", max_length=32),
):
    try:
        request = await prepare_request(task, prompt, mode, aspect_ratio, seed, num_steps,
            is_transparent, preserve, region, image, mask, reference, references,
            lora_enabled=lora_enabled, lora_strength=lora_strength,
            pose_enabled=pose_enabled, pose_reference=pose_reference, pose_reviewed=pose_reviewed,
            pose_order=pose_order, mask_reviewed=mask_reviewed, repair_kind=repair_kind, repair_parent_id=repair_parent_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    job_id = uuid.uuid4().hex
    ACTIVE_PROMPTS[job_id] = request["prompt"]
    job = {"job_id": job_id, "status": "queued", "phase": "Waiting for the local GPU",
           "created_at": time.time(), "started_at": None, "finished_at": None,
           "progress": None, "error": None, "request": request, "result": None}
    event = threading.Event()
    CANCEL_EVENTS[job_id] = event
    try:
        save_new_job(job)
    except HTTPException:
        ACTIVE_PROMPTS.pop(job_id, None)
        CANCEL_EVENTS.pop(job_id, None)
        raise
    EXECUTOR.submit(process_job, job_id, event)
    return public_job(job)


@app.post("/api/jobs/batch", status_code=202)
async def submit_batch(
    prompts: str = Form(..., description="JSON array of 1 to 5 prompts or newline separated"),
    task: str = Form("generate"),
    mode: Literal["fast", "balanced", "ultra"] = Form("balanced"),
    aspect_ratio: Literal["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"] = Form("1:1"),
    seed: int = Form(42, ge=0, le=2**63-1),
    num_steps: Optional[int] = Form(None, ge=1, le=100),
    is_transparent: bool = Form(False),
    preserve: bool = Form(True),
    region: str = Form("auto"),
    image: Optional[UploadFile] = File(None),
    mask: Optional[UploadFile] = File(None),
    reference: Optional[UploadFile] = File(None),
    references: Optional[list[UploadFile]] = File(None),
    lora_enabled: bool = Form(True),
    lora_strength: float = Form(0.8),
    pose_enabled: bool = Form(False), pose_reference: Optional[UploadFile] = File(None),
    pose_reviewed: bool = Form(False), pose_order: Literal["same", "swap"] = Form("same"),
    mask_reviewed: bool = Form(False), repair_kind: Literal["none", "hand", "leg", "contact"] = Form("none"),
    repair_parent_id: str = Form("", max_length=32),
):
    try:
        raw_prompts = json.loads(prompts) if prompts.strip().startswith("[") else [p.strip() for p in prompts.split("\n")]
    except Exception as exc:
        raise HTTPException(422, f"Could not parse prompts: {exc}")

    if not isinstance(raw_prompts, list):
        raise HTTPException(422, "Prompts must be provided as a list.")

    clean_prompts = [str(p).strip() for p in raw_prompts if str(p).strip()]
    if not (1 <= len(clean_prompts) <= 5):
        raise HTTPException(422, "Please provide between 1 and 5 prompts.")
    if repair_kind != "none":
        raise HTTPException(422, "Repair one selected result at a time; batch repair is disabled")

    for p in clean_prompts:
        if len(p) > 4000:
            raise HTTPException(422, "Each prompt must not exceed 4000 characters.")

    try:
        base_request = await prepare_request(
            task, clean_prompts[0], mode, aspect_ratio, seed, num_steps,
            is_transparent, preserve, region, image, mask, reference, references,
            lora_enabled=lora_enabled, lora_strength=lora_strength,
            pose_enabled=pose_enabled, pose_reference=pose_reference, pose_reviewed=pose_reviewed,
            pose_order=pose_order, mask_reviewed=mask_reviewed
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    batch_id = uuid.uuid4().hex
    job_ids = []
    jobs = []

    for idx, prompt_text in enumerate(clean_prompts):
        req = dict(base_request)
        req["prompt"] = prompt_text
        req["seed"] = (seed + idx) % (2**63 - 1)
        job_id = uuid.uuid4().hex
        ACTIVE_PROMPTS[job_id] = prompt_text
        job = {
            "job_id": job_id,
            "batch_id": batch_id,
            "batch_index": idx,
            "batch_total": len(clean_prompts),
            "status": "queued",
            "phase": "Waiting for the local GPU",
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "progress": None,
            "error": None,
            "request": req,
            "result": None,
        }
        event = threading.Event()
        CANCEL_EVENTS[job_id] = event
        save_job(job)
        EXECUTOR.submit(process_job, job_id, event)
        job_ids.append(job_id)
        jobs.append(public_job(job))

    batch = {
        "batch_id": batch_id,
        "job_ids": job_ids,
        "total": len(job_ids),
        "created_at": time.time(),
    }
    save_batch(batch)

    return {
        "batch_id": batch_id,
        "job_ids": job_ids,
        "total": len(job_ids),
        "jobs": jobs,
    }


@app.get("/api/batches/{batch_id}")
def batch_status(batch_id: str):
    batch = get_batch(batch_id)
    jobs = []
    completed = 0
    running_idx = None
    all_statuses = []
    for idx, jid in enumerate(batch["job_ids"]):
        try:
            pj = public_job(get_job(jid))
            jobs.append(pj)
            all_statuses.append(pj["status"])
            if pj["status"] in ("succeeded", "failed", "cancelled"):
                completed += 1
            elif pj["status"] == "running" and running_idx is None:
                running_idx = idx
        except HTTPException:
            pass

    if running_idx is None and completed < len(jobs):
        for idx, j in enumerate(jobs):
            if j["status"] == "queued":
                running_idx = idx
                break

    if all(s == "cancelled" for s in all_statuses):
        overall = "cancelled"
    elif all(s == "succeeded" for s in all_statuses):
        overall = "succeeded"
    elif all(s in ("succeeded", "failed", "cancelled") for s in all_statuses):
        overall = "completed"
    elif "running" in all_statuses:
        overall = "running"
    elif "cancelling" in all_statuses:
        overall = "cancelling"
    else:
        overall = "queued"

    return {
        "batch_id": batch_id,
        "total": len(jobs),
        "completed": completed,
        "active_index": running_idx,
        "overall_status": overall,
        "jobs": jobs,
        "created_at": batch["created_at"],
    }


@app.post("/api/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str):
    batch = get_batch(batch_id)
    for jid in batch["job_ids"]:
        try:
            cancel_job(jid)
        except HTTPException:
            pass
    return batch_status(batch_id)


def public_job(job):
    job = dict(job)
    req = dict(job.get("request", {}))
    for key in ("source_path", "mask_path", "reference_paths"):
        req.pop(key, None)
    # SECURITY: Never return the user's prompt via GET or public endpoints
    req["prompt"] = "[Protected]"
    job["request"] = req
    job["prompt"] = "[Protected]"
    job["mode"] = req.get("mode", "balanced")
    if job.get("result"):
        res = dict(job["result"])
        if "metadata" in res and isinstance(res["metadata"], dict):
            res_meta = dict(res["metadata"])
            res_meta.pop("compiled_prompt", None)
            res["metadata"] = res_meta
        job.update(res)
    start = job.get("started_at")
    job["elapsed_sec"] = round((job.get("finished_at") or time.time()) - start, 1) if start else 0
    return job


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    return public_job(get_job(job_id))


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = get_job(job_id)
    if job["status"] in ("queued", "running", "cancelling"):
        event = CANCEL_EVENTS.get(job_id)
        if event:
            event.set()
            if job["status"] == "queued":
                job = update_job(job_id, status="cancelled", phase="Cancelled", finished_at=time.time())
            else:
                job = update_job(job_id, status="cancelling", phase="Stopping this job")
    return public_job(job)


@app.get("/api/jobs/{job_id}/workflow")
def download_workflow(job_id: str):
    raise HTTPException(403, "Workflow export is disabled for prompt privacy.")


@app.get("/api/history")
def history():
    with connection() as db:
        rows = db.execute("SELECT data FROM jobs ORDER BY created DESC LIMIT 40").fetchall()
    items = [public_job(json.loads(row[0])) for row in rows]
    listed = {item.get("image_url") for item in items}
    # Keep earlier local-Qwen Studio outputs usable after the upgrade.
    for path in sorted(Path(OUTPUT_DIR).glob("qwen_*.png"), key=lambda p: p.stat().st_mtime, reverse=True):
        url = f"/outputs/{path.name}"
        if url in listed:
            continue
        items.append({"image_url": url, "prompt": "[Protected]", "created_at": path.stat().st_mtime,
                      "status": "succeeded", "metadata": {}})
        if len(items) >= 40:
            break
    return {"items": items}


def process_job(job_id, cancel):
    uploaded = []
    try:
        with RESOURCE_LOCK:
            if cancel.is_set():
                update_job(job_id, status="cancelled", phase="Cancelled", finished_at=time.time())
                return
            job_record = get_job(job_id)
            req = dict(job_record["request"])
            prompt_text = ACTIVE_PROMPTS.get(job_id, "") or req.get("prompt", "")
            update_job(job_id, status="running", phase="Preparing Qwen", started_at=time.time())
            prepare_qwen_memory()
            source = mask = None
            if req["source_path"]:
                with Image.open(req["source_path"]) as im:
                    source = im.copy()
            if req["mask_path"]:
                with Image.open(req["mask_path"]) as im:
                    mask = im.convert("L")
            crop = regions.crop_bounds(mask, context=.5 if req.get("repair_kind", "none") != "none" else .35) if source and mask and req["mode"] == "ultra" and not req.get("pose_enabled") else None
            working = source.crop(crop) if crop else source
            inputs = [working] if working else []
            for ref in req["reference_paths"]:
                with Image.open(ref) as im:
                    inputs.append(im.copy())
            names, alpha_flags = [], []
            for i, im in enumerate(inputs):
                dest = COMFY_INPUT_DIR / f"{job_id}_{i}.png"
                im.save(dest)
                uploaded.append(dest)
                names.append(f"studio_uploads/{dest.name}")
                alpha_flags.append("A" in im.getbands())
            clean = req["task"] == "generate" and req.get("lora_enabled", True)
            compiled = compile_prompt(prompt_text, req["region"], preserve=bool(source and req["preserve"]),
                                      transparent=req["transparent"], reference=max(0, len(names)-1),
                                      clean_prompt=clean, pose_enabled=req.get("pose_enabled", False),
                                      pose_order=req.get("pose_order", "same"), task=req["task"], repair_kind=req.get("repair_kind", "none"))
            mask_name = None
            if mask:
                sampling_mask = mask.crop(crop) if crop else mask
                dest = COMFY_INPUT_DIR / f"{job_id}_mask.png"
                sampling_mask.save(dest)
                uploaded.append(dest)
                mask_name = f"studio_uploads/{dest.name}"
            lora_name = engine.LORA_NAME if req.get("lora_enabled", True) else None
            lora_strength = float(req.get("lora_strength", 0.8))
            workflow = engine.build_workflow(compiled, mode=req["mode"], seed=req["seed"],
                aspect_ratio=req["aspect_ratio"], steps=req["num_steps"],
                source_name=names[0] if names else None, source_size=working.size if working else None,
                source_has_alpha=alpha_flags[0] if names else False, reference_names=tuple(names[1:]), mask_name=mask_name,
                lora_name=lora_name, lora_strength=lora_strength)

            stage_clock = time.monotonic()
            active_stage = "preparing"
            timings = {}
            def record_stage(stage):
                nonlocal active_stage, stage_clock
                if stage != active_stage:
                    now = time.monotonic()
                    timings[active_stage] = timings.get(active_stage, 0) + now - stage_clock
                    active_stage, stage_clock = stage, now

            def progress(update):
                if isinstance(update, dict):
                    if update.get("stage"):
                        record_stage(update["stage"])
                    allowed = {k: v for k, v in update.items() if k in ("phase", "progress", "prompt_id")}
                    if "stage" in update:
                        allowed["phase"] = {"queued": "Waiting for ComfyUI", "preparing": "Preparing Qwen",
                            "encoding": "Understanding the image and instruction", "sampling": "Generating detail",
                            "decoding": "Decoding the image", "saving": "Saving image", "running": "Running Qwen"}.get(update["stage"], "Running Qwen")
                        if update["stage"] != "sampling":
                            allowed["progress"] = None
                    if update.get("step") is not None and update.get("steps"):
                        counter = {"sampling": "Sampling", "decoding": "Decoding tiles", "encoding": "Encoding tiles"}.get(update.get("stage"), "Processing")
                        allowed.update(progress=update["step"] / update["steps"],
                                       phase=f"{counter}: {update['step']} / {update['steps']}")
                    if allowed:
                        update_job(job_id, **allowed)
            output = CLIENT.run(workflow, progress=progress, cancel_event=cancel,
                on_submitted=lambda pid: update_job(job_id, prompt_id=pid))
            record_stage("finishing")
            if cancel.is_set():
                update_job(job_id, status="cancelled", phase="Cancelled", finished_at=time.time())
                return
            update_job(job_id, phase="Finishing image")
            try:
                with Image.open(output) as im:
                    generated = im.copy()
            finally:
                # SECURITY: Immediately remove generated image from ComfyUI output directory!
                try:
                    output.unlink(missing_ok=True)
                except Exception:
                    pass
                CLIENT.wipe_history()

            protected = bool(source and mask)
            if protected:
                generated = regions.composite_preserving(source, generated, mask, feather=6, crop_box=crop)
            metadata = {"model": "Qwen-Image-2.1 INT8", "mode": req["mode"], "seed": req["seed"],
                "is_transparent": req["transparent"],
                "steps": workflow["7"]["inputs"]["steps"], "dimensions": list(generated.size),
                "target": req["region"], "crop_box": crop,
                "elapsed_sec": round(time.time() - get_job(job_id)["started_at"], 1),
                "stage_timings_sec": {key: round(value, 2) for key, value in timings.items()},
                "preservation": {"method": "original-pixel composite" if protected else "native Qwen conditioning",
                                 "protected_pixels_unchanged": protected}, "warnings": []}
            if protected:
                checked = pose_assist.preservation_check(source, generated, mask)
                metadata["preservation"].update(checked)
                metadata["preservation"]["protected_pixels_unchanged"] = checked.get("exact_outside_preservation", False)
                if not metadata["preservation"]["protected_pixels_unchanged"]:
                    raise ImageGenerationError("Protected pixels changed during compositing; this result was not accepted")
            metadata["pose_assist"] = {"enabled": req.get("pose_enabled", False), "route": "native_reference" if req.get("pose_enabled") else "none",
                "reviewed": req.get("pose_reviewed", False), "anatomy_verified": False}
            metadata["repair"] = {"kind": req.get("repair_kind", "none"), "parent_id": req.get("repair_parent_id", ""), "automatic": False}
            if req.get("pose_enabled"):
                metadata["warnings"].append("Pose Assist is native image-reference guidance, not ControlNet. Review identity, pose, contact and anatomy; reference matching is not guaranteed.")
            if source and not protected:
                metadata["warnings"].append("Global edits may change unmentioned details. A reviewed region mask retains pixels outside the edit.")
            if source and mask and prompt_intent.analyze(prompt_text)["changes_pose"]:
                metadata["warnings"].append("Movement is limited to the reviewed mask. It must cover moved limbs, the contact region and any exposed background; correct anatomy/contact has not been visually verified.")
            if generated.mode != "RGBA" and req["transparent"]:
                metadata["warnings"].append("This result has no alpha channel; try a simpler isolated-subject transparency prompt.")

            filename = f"qwen_{job_id}.png"
            # SECURITY: Strip all metadata from generated image. Never write prompt into PNG info!
            clean_generated = generated.copy()
            clean_generated.info.clear()
            clean_generated.save(Path(OUTPUT_DIR) / filename, format="PNG")

            result = {"image_url": f"/outputs/{filename}", "source_url": req["source_url"],
                      "mask_url": req["mask_url"], "metadata": metadata}
            update_job(job_id, status="succeeded", phase="Complete", progress=1.0, result=result, finished_at=time.time())
    except Exception as exc:
        stopped = cancel.is_set()
        update_job(job_id, status="cancelled" if stopped else "failed", phase="Cancelled" if stopped else "Failed",
                   error=None if stopped else str(exc), finished_at=time.time())
    finally:
        # SECURITY: Delete all uploaded temporary images from ComfyUI input directory
        for p in uploaded:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
        CANCEL_EVENTS.pop(job_id, None)
        ACTIVE_PROMPTS.pop(job_id, None)
        CLIENT.wipe_history()


@app.post("/api/edit")
async def legacy_edit(image: UploadFile = File(...), prompt: str = Form(..., min_length=1, max_length=4000),
                      num_steps: int = Form(20, ge=1, le=100), seed: int = Form(42, ge=0),
                      is_transparent: bool = Form(False)):
    source = open_image(await image.read(25 * 1024 * 1024 + 1))
    path = COMFY_INPUT_DIR / f"legacy_{uuid.uuid4().hex}.png"
    source.save(path)
    compiled = compile_prompt(prompt, preserve=True, transparent=is_transparent)
    def run():
        with RESOURCE_LOCK:
            return _run_comfy_workflow(engine.build_workflow(compiled, mode="fast", steps=num_steps, seed=seed,
                source_name=f"studio_uploads/{path.name}", source_size=source.size, source_has_alpha="A" in source.getbands()))
    try:
        url = await asyncio.to_thread(run)
    except ImageGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)
        CLIENT.wipe_history()
    return {"status": "success", "image_url": url}


class RevealRequest(BaseModel):
    path: Optional[str] = None
    filename: Optional[str] = None


@app.post("/api/windows/open-folder")
def open_outputs_folder():
    """Open the local outputs directory in Windows File Explorer."""
    out_dir = Path(OUTPUT_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        try:
            os.startfile(str(out_dir))
            return {"status": "success", "opened": str(out_dir)}
        except Exception:
            subprocess.Popen(["explorer.exe", str(out_dir)])
            return {"status": "success", "opened": str(out_dir)}
    return {"status": "unsupported", "platform": os.name}


@app.post("/api/windows/reveal")
def reveal_output_file(req: RevealRequest):
    """Highlight and select a generated file in Windows File Explorer."""
    target_name = req.filename
    if not target_name and req.path:
        target_name = Path(req.path).name
    if not target_name:
        raise HTTPException(400, "Missing filename or path")

    clean_name = Path(target_name).name
    target_path = (Path(OUTPUT_DIR) / clean_name).resolve()
    if not target_path.exists():
        raise HTTPException(404, f"File not found: {clean_name}")

    if os.name == "nt":
        subprocess.Popen(["explorer.exe", f"/select,{str(target_path)}"])
        return {"status": "success", "revealed": str(target_path)}
    return {"status": "unsupported", "platform": os.name}


@app.get("/api/windows/system-stats")
def windows_system_stats():
    """Return live Windows GPU, VRAM and system memory utilization."""
    gpu = None
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=1.5
        )
        if proc.returncode == 0 and proc.stdout.strip():
            parts = [p.strip() for p in proc.stdout.strip().split(",")]
            if len(parts) >= 4:
                total_mb = float(parts[1])
                used_mb = float(parts[2])
                free_mb = float(parts[3])
                gpu = {
                    "name": parts[0],
                    "vram_total_gb": round(total_mb / 1024, 2),
                    "vram_used_gb": round(used_mb / 1024, 2),
                    "vram_free_gb": round(free_mb / 1024, 2),
                    "vram_percent": round((used_mb / total_mb) * 100, 1) if total_mb > 0 else 0
                }
    except Exception:
        pass

    ram = None
    try:
        mem = psutil.virtual_memory()
        ram = {
            "total_gb": round(mem.total / (1024**3), 1),
            "used_gb": round(mem.used / (1024**3), 1),
            "free_gb": round(mem.available / (1024**3), 1),
            "percent": mem.percent
        }
    except Exception:
        pass

    return {
        "status": "ready",
        "gpu": gpu,
        "ram": ram,
        "platform": "windows" if os.name == "nt" else sys.platform
    }


app.mount("/web", StaticFiles(directory=PROJECT_DIR / "web"), name="web")
app.mount("/assets", StaticFiles(directory=PROJECT_DIR / "assets"), name="assets")
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")
