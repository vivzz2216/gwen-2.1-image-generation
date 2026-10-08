"""Persistent local Qwen jobs and region-preserving edit orchestration."""
import asyncio
import io
import json
import os
import shutil
import sqlite3
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

import qwen_engine as engine
import editing_regions as regions

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


@contextmanager
def connection():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL)")
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
    yield


app = FastAPI(title="Qwen Image 2.1 Studio", version="3.0.0", lifespan=lifespan)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    aspect_ratio: Literal["1:1", "16:9", "9:16", "4:3", "3:2"] = "1:1"
    mode: Literal["fast", "balanced", "ultra"] = "balanced"
    is_transparent: bool = False
    num_steps: Optional[int] = Field(default=None, ge=1, le=100)
    seed: int = Field(default=42, ge=0, le=2**63-1)
    enhance_prompt: bool = False

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Enter a prompt")
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


def compile_prompt(instruction, target="global", preserve=True, transparent=False, reference=False):
    text = instruction.strip()
    if preserve:
        protected = ["the person's identity and recognizable facial features", "expression", "pose and body proportions",
                     "hand positions", "camera framing and perspective", "lighting and shadows"]
        if target != "background":
            protected.append("background and surroundings")
        if target != "hair":
            protected.append("hairstyle")
        text = (f"Edit Picture 1 according to this instruction: {text}\n"
                f"Change only the requested {target if target not in ('manual', 'global') else 'elements'}. "
                "Keep all unmentioned elements unchanged, including " + ", ".join(protected) + ". "
                "Match the original material lighting, perspective, scale and occlusion. "
                "Do not beautify the face, reposition the subject, or restyle the whole photograph.")
    if reference:
        text += " Use Picture 2 only as an appearance reference for the requested change. Picture 1 determines identity, pose, composition, and background."
    if transparent:
        text = f"This is an RGBA image with transparency. {text} The image has an alpha channel and the background is transparent."
    return text


import urllib.request

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def query_ollama_enhance(prompt: str) -> str:
    prompt = prompt.strip()
    if not prompt:
        return ""

    try:
        system_instruction = (
            "You are an expert prompt engineer for the local Qwen-Image-2.1 diffusion model. "
            "Transform the following idea into an evocative, highly detailed, photorealistic visual scene prompt. "
            "Describe the subject, composition, camera lens/angle, lighting, realistic textures, and background ambiance. "
            "Output ONLY the enhanced visual prompt text. Do not include markdown headers, quotes, or conversational remarks."
        )
        payload = {
            "model": "dolphin-llama3",
            "prompt": f"{system_instruction}\n\nUser Idea: {prompt}\n\nEnhanced Prompt:",
            "stream": False,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9,
                "num_predict": 180,
                "stop": ["\nUser Idea:", "\n\nIdea:", "---"]
            }
        }
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                enhanced = str(data.get("response", "")).strip()
                if enhanced:
                    if (enhanced.startswith('"') and enhanced.endswith('"')) or (enhanced.startswith("'") and enhanced.endswith("'")):
                        enhanced = enhanced[1:-1].strip()
                    return enhanced
    except Exception:
        pass

    return prompt + ". Clear subject definition, coherent composition, detailed textures and lighting consistent with the described scene."


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((PROJECT_DIR / "web" / "index.html").read_text(encoding="utf-8"))


@app.get("/api/capabilities")
def capabilities():
    try:
        stats = CLIENT.request("/system_stats")
        backend = {"status": "ready", "gpu": stats.get("devices", [{}])[0].get("name", "CUDA"),
                   "ram_free": stats.get("system", {}).get("ram_free"), "url": COMFY_URL}
    except Exception:
        backend = {"status": "offline", "url": COMFY_URL, "message": "Start run_qwen_comfyui.bat"}

    enhancer = {"status": "offline", "model": "dolphin-llama3", "installed": False, "url": OLLAMA_URL}
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=1) as resp:
            if resp.status == 200:
                models = [m.get("name", "") for m in json.loads(resp.read().decode("utf-8")).get("models", [])]
                has_dolphin = any("dolphin-llama3" in m for m in models)
                enhancer = {
                    "status": "ready" if has_dolphin else "model_missing",
                    "model": "dolphin-llama3",
                    "installed": has_dolphin,
                    "url": OLLAMA_URL
                }
    except Exception:
        pass

    return {"modes": engine.mode_info(), "backend": backend, "enhancer": enhancer, "segmentation": regions.segmenter_status(),
            "max_reference_images": 3, "max_upload_mb": 25}


@app.post("/api/enhance")
def enhance_endpoint(req: dict):
    prompt = str(req.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(422, "Enter a prompt first")
    return {"original": prompt, "enhanced": query_ollama_enhance(prompt)}


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


def generate_live_ai_image(prompt, aspect_ratio="1:1", seed=42, enhance=False, num_steps=None, mode="balanced"):
    return _run_comfy_workflow(engine.build_workflow(prompt, mode=mode, seed=seed, aspect_ratio=aspect_ratio, steps=num_steps))


@app.post("/api/generate")
async def generate_endpoint(req: GenerateRequest):
    prompt = compile_prompt(req.prompt, preserve=False, transparent=req.is_transparent)
    def run():
        with RESOURCE_LOCK:
            return generate_live_ai_image(prompt, req.aspect_ratio, req.seed, req.enhance_prompt, req.num_steps, req.mode)
    try:
        url = await asyncio.to_thread(run)
    except ImageGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"status": "success", "image_url": url, "mode": req.mode, "aspect_ratio": req.aspect_ratio}


async def prepare_request(task, prompt, mode, aspect_ratio, seed, num_steps, transparent, preserve,
                          region, image, mask, reference, references):
    settings = GenerateRequest(prompt=prompt, mode=mode, aspect_ratio=aspect_ratio, seed=seed,
                               num_steps=num_steps, is_transparent=transparent)
    if task not in ("generate", "edit"):
        raise HTTPException(422, "Choose Generate or Edit")
    if task == "edit" and not image:
        raise HTTPException(422, "Choose a source image to edit")
    if transparent and task == "edit" and preserve and region != "global":
        raise HTTPException(422, "To remove the background choose a global edit. Protected edits retain original pixels outside their masks.")
    source = open_image(await image.read(25 * 1024 * 1024 + 1)) if image else None
    source_path = source_url = mask_path = mask_url = None
    if source:
        source_path, source_url = save_image(source, "source")
    target = region
    if task == "edit" and preserve and region != "global":
        if mask is None:
            raise HTTPException(422, "Preview or paint a mask, review it, and accept it before running a protected edit.")
        raw = open_image(await mask.read(25 * 1024 * 1024 + 1))
        try:
            selected = regions.normalize_manual_mask(raw, source.size)
            target = regions.detect_edit_target(prompt, target=region) if region != "manual" else "manual"
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        mask_path, mask_url = save_image(selected, "mask")
    refs = ([reference] if reference else []) + (references or [])
    if len(refs) > 2:
        raise HTTPException(422, "Use up to two extra reference images on this 6 GB GPU")
    if refs and task != "edit":
        raise HTTPException(422, "Additional references are available in Edit mode")
    reference_paths = []
    for ref in refs:
        normalized = open_image(await ref.read(25 * 1024 * 1024 + 1))
        ref_path, _ = save_image(normalized, "reference")
        reference_paths.append(str(ref_path))
    return {"task": task, "prompt": settings.prompt, "mode": mode, "aspect_ratio": aspect_ratio,
            "seed": seed, "num_steps": num_steps, "transparent": transparent, "preserve": preserve,
            "region": target, "source_path": str(source_path) if source_path else None, "source_url": source_url,
            "mask_path": str(mask_path) if mask_path else None, "mask_url": mask_url, "reference_paths": reference_paths}


@app.post("/api/jobs", status_code=202)
async def submit_job(
    task: str = Form("generate"), prompt: str = Form(..., min_length=1, max_length=4000),
    mode: Literal["fast", "balanced", "ultra"] = Form("balanced"),
    aspect_ratio: Literal["1:1", "16:9", "9:16", "4:3", "3:2"] = Form("1:1"),
    seed: int = Form(42, ge=0, le=2**63-1), num_steps: Optional[int] = Form(None, ge=1, le=100),
    is_transparent: bool = Form(False), preserve: bool = Form(True), region: str = Form("auto"),
    image: Optional[UploadFile] = File(None), mask: Optional[UploadFile] = File(None),
    reference: Optional[UploadFile] = File(None), references: Optional[list[UploadFile]] = File(None),
):
    try:
        request = await prepare_request(task, prompt, mode, aspect_ratio, seed, num_steps,
            is_transparent, preserve, region, image, mask, reference, references)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    job_id = uuid.uuid4().hex
    ACTIVE_PROMPTS[job_id] = request["prompt"]
    job = {"job_id": job_id, "status": "queued", "phase": "Waiting for the local GPU",
           "created_at": time.time(), "started_at": None, "finished_at": None,
           "progress": None, "error": None, "request": request, "result": None}
    event = threading.Event()
    CANCEL_EVENTS[job_id] = event
    save_job(job)
    EXECUTOR.submit(process_job, job_id, event)
    return public_job(job)


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
            source = mask = None
            if req["source_path"]:
                with Image.open(req["source_path"]) as im:
                    source = im.copy()
            if req["mask_path"]:
                with Image.open(req["mask_path"]) as im:
                    mask = im.convert("L")
            crop = regions.crop_bounds(mask, context=.35) if source and mask and req["mode"] == "ultra" else None
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
            compiled = compile_prompt(prompt_text, req["region"], preserve=bool(source and req["preserve"]),
                                      transparent=req["transparent"], reference=len(names)>1)
            mask_name = None
            if mask:
                sampling_mask = mask.crop(crop) if crop else mask
                dest = COMFY_INPUT_DIR / f"{job_id}_mask.png"
                sampling_mask.save(dest)
                uploaded.append(dest)
                mask_name = f"studio_uploads/{dest.name}"
            workflow = engine.build_workflow(compiled, mode=req["mode"], seed=req["seed"],
                aspect_ratio=req["aspect_ratio"], steps=req["num_steps"],
                source_name=names[0] if names else None, source_size=working.size if working else None,
                source_has_alpha=alpha_flags[0] if names else False, reference_names=tuple(names[1:]), mask_name=mask_name)

            def progress(update):
                if isinstance(update, dict):
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
                "preservation": {"method": "original-pixel composite" if protected else "native Qwen conditioning",
                                 "protected_pixels_unchanged": protected}, "warnings": []}
            if source and not protected:
                metadata["warnings"].append("Global edits may change unmentioned details. A reviewed region mask retains pixels outside the edit.")
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


app.mount("/web", StaticFiles(directory=PROJECT_DIR / "web"), name="web")
app.mount("/assets", StaticFiles(directory=PROJECT_DIR / "assets"), name="assets")
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")
